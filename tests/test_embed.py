"""Tests for app.embed — all requests mocked, no network, no secrets."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import embed
from app.embed import EmbedError, get_aad_token, get_embed_token


@pytest.fixture(autouse=True)
def _reset():
    embed._reset_cache()
    yield
    embed._reset_cache()


def _cfg():
    return SimpleNamespace(
        POWERBI_TENANT_ID="tenant-id",
        POWERBI_CLIENT_ID="client-id",
        POWERBI_CLIENT_SECRET="client-secret",
        POWERBI_WORKSPACE_ID="workspace-id",
        POWERBI_REPORT_ID="report-id",
    )


def _resp(status_code=200, json_body=None):
    resp = SimpleNamespace(status_code=status_code)
    resp.json = lambda: json_body
    return resp


def _future_iso(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


class TestGetAadToken:
    def test_fetches_and_caches_across_calls(self):
        cfg = _cfg()
        aad_resp = _resp(200, {"access_token": "aad-token-1", "expires_in": 3600})
        with patch("app.embed.requests.post", return_value=aad_resp) as mock_post:
            token1 = get_aad_token(cfg)
            token2 = get_aad_token(cfg)

        assert token1 == "aad-token-1"
        assert token2 == "aad-token-1"
        mock_post.assert_called_once()

    def test_form_encoded_client_credentials_request(self):
        cfg = _cfg()
        aad_resp = _resp(200, {"access_token": "tok", "expires_in": 3600})
        with patch("app.embed.requests.post", return_value=aad_resp) as mock_post:
            get_aad_token(cfg)

        _, kwargs = mock_post.call_args
        assert kwargs["data"] == {
            "grant_type": "client_credentials",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scope": "https://analysis.windows.net/powerbi/api/.default",
        }

    def test_refreshes_after_near_expiry(self):
        cfg = _cfg()
        first = _resp(200, {"access_token": "aad-token-1", "expires_in": 60})
        second = _resp(200, {"access_token": "aad-token-2", "expires_in": 3600})
        with patch("app.embed.requests.post", side_effect=[first, second]) as mock_post:
            token1 = get_aad_token(cfg)
            # expires_in=60s is inside the 5-minute refresh margin, so the
            # very next call must refetch rather than reuse the cache.
            token2 = get_aad_token(cfg)

        assert token1 == "aad-token-1"
        assert token2 == "aad-token-2"
        assert mock_post.call_count == 2

    def test_non_2xx_raises_embed_error(self):
        cfg = _cfg()
        with patch("app.embed.requests.post", return_value=_resp(401, {})):
            with pytest.raises(EmbedError):
                get_aad_token(cfg)

    def test_request_exception_raises_embed_error(self):
        cfg = _cfg()
        import requests

        with patch(
            "app.embed.requests.post", side_effect=requests.ConnectionError("boom")
        ):
            with pytest.raises(EmbedError):
                get_aad_token(cfg)


class TestGetEmbedToken:
    def _mock_upstream(self, expiration_minutes=60):
        aad_resp = _resp(200, {"access_token": "aad-token", "expires_in": 3600})
        report_resp = _resp(
            200,
            {"embedUrl": "https://app.powerbi.com/reportEmbed", "datasetId": "ds-1"},
        )
        gen_resp = _resp(
            200,
            {"token": "embed-token", "expiration": _future_iso(expiration_minutes)},
        )
        return aad_resp, report_resp, gen_resp

    def test_returns_expected_shape_and_reads_dataset_id_from_report(self):
        cfg = _cfg()
        aad_resp, report_resp, gen_resp = self._mock_upstream()
        with patch("app.embed.requests.post", side_effect=[aad_resp, gen_resp]) as mock_post, \
             patch("app.embed.requests.get", return_value=report_resp) as mock_get:
            result = get_embed_token(cfg)

        assert result == {
            "token": "embed-token",
            "embedUrl": "https://app.powerbi.com/reportEmbed",
            "reportId": "report-id",
            "expiresAt": result["expiresAt"],
        }
        mock_get.assert_called_once()
        # GenerateToken body must use the dataset id read from the report
        # response, never a hardcoded literal.
        _, gen_kwargs = mock_post.call_args_list[1]
        assert gen_kwargs["json"]["datasets"] == [{"id": "ds-1"}]
        assert gen_kwargs["json"]["reports"] == [{"id": "report-id"}]
        assert gen_kwargs["json"]["targetWorkspaces"] == [{"id": "workspace-id"}]

    def test_cached_across_calls(self):
        cfg = _cfg()
        aad_resp, report_resp, gen_resp = self._mock_upstream()
        with patch("app.embed.requests.post", side_effect=[aad_resp, gen_resp]) as mock_post, \
             patch("app.embed.requests.get", return_value=report_resp) as mock_get:
            result1 = get_embed_token(cfg)
            result2 = get_embed_token(cfg)

        assert result1 == result2
        assert mock_get.call_count == 1
        assert mock_post.call_count == 2

    def test_refreshes_after_near_expiry(self):
        cfg = _cfg()
        aad1, report1, gen1 = self._mock_upstream(expiration_minutes=1)
        _, report2, gen2 = self._mock_upstream(expiration_minutes=60)
        # The AAD token itself (expires_in=3600s) is still fresh on the
        # second call, so only the embed token/report calls repeat.
        with patch(
            "app.embed.requests.post", side_effect=[aad1, gen1, gen2]
        ) as mock_post, patch(
            "app.embed.requests.get", side_effect=[report1, report2]
        ) as mock_get:
            get_embed_token(cfg)
            # expiration in 1 minute is inside the 5-minute refresh margin.
            get_embed_token(cfg)

        assert mock_get.call_count == 2
        assert mock_post.call_count == 3

    def test_report_fetch_failure_raises_embed_error(self):
        cfg = _cfg()
        aad_resp = _resp(200, {"access_token": "aad-token", "expires_in": 3600})
        with patch("app.embed.requests.post", return_value=aad_resp), \
             patch("app.embed.requests.get", return_value=_resp(404, {})):
            with pytest.raises(EmbedError):
                get_embed_token(cfg)

    def test_generate_token_failure_raises_embed_error(self):
        cfg = _cfg()
        aad_resp = _resp(200, {"access_token": "aad-token", "expires_in": 3600})
        report_resp = _resp(
            200,
            {"embedUrl": "https://app.powerbi.com/reportEmbed", "datasetId": "ds-1"},
        )
        with patch(
            "app.embed.requests.post", side_effect=[aad_resp, _resp(500, {})]
        ), patch("app.embed.requests.get", return_value=report_resp):
            with pytest.raises(EmbedError):
                get_embed_token(cfg)

    def test_malformed_report_response_raises_embed_error(self):
        cfg = _cfg()
        aad_resp = _resp(200, {"access_token": "aad-token", "expires_in": 3600})
        with patch("app.embed.requests.post", return_value=aad_resp), \
             patch("app.embed.requests.get", return_value=_resp(200, {"embedUrl": "x"})):
            with pytest.raises(EmbedError):
                get_embed_token(cfg)

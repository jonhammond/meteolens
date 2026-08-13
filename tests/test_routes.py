"""Tests for the /api/embed-token and / routes. No network, no secrets.

The app factory validates REQUIRED_VARS at construction time (app/config.py),
so every test builds a minimal fake environment covering just those required
vars, then adds/omits the five POWERBI_* optional vars to control
embed_configured.
"""

from unittest.mock import patch

import pytest

from app import create_app
from app.embed import EmbedError

REQUIRED_ENV = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "service-role-key",
    "POWERBI_PUSH_URL": "https://example.invalid/push",
    "INGEST_TOKEN": "ingest-token",
}

EMBED_ENV = {
    "POWERBI_TENANT_ID": "tenant-id",
    "POWERBI_CLIENT_ID": "client-id",
    "POWERBI_CLIENT_SECRET": "client-secret",
    "POWERBI_WORKSPACE_ID": "workspace-id",
    "POWERBI_REPORT_ID": "report-id",
}


def _make_app(embed_configured):
    env = dict(REQUIRED_ENV)
    if embed_configured:
        env.update(EMBED_ENV)
    app = create_app(env)
    app.testing = True
    return app


@pytest.fixture
def _no_readings():
    with patch("app.routes.db.build_client", return_value=None), patch(
        "app.routes.db.fetch_latest_per_location", return_value=[]
    ):
        yield


class TestEmbedTokenRoute:
    def test_unconfigured_returns_503(self):
        app = _make_app(embed_configured=False)
        client = app.test_client()

        resp = client.get("/api/embed-token")

        assert resp.status_code == 503
        assert "error" in resp.get_json()

    def test_configured_returns_exactly_four_keys(self):
        app = _make_app(embed_configured=True)
        client = app.test_client()
        fake_result = {
            "token": "embed-token",
            "embedUrl": "https://app.powerbi.com/reportEmbed",
            "reportId": "report-id",
            "expiresAt": "2026-08-12T21:00:00+00:00",
        }
        with patch("app.routes.embed.get_embed_token", return_value=fake_result):
            resp = client.get("/api/embed-token")

        assert resp.status_code == 200
        body = resp.get_json()
        assert set(body.keys()) == {"token", "embedUrl", "reportId", "expiresAt"}
        assert body == fake_result

    def test_upstream_failure_returns_503_without_detail(self):
        app = _make_app(embed_configured=True)
        client = app.test_client()

        with patch(
            "app.routes.embed.get_embed_token",
            side_effect=EmbedError("client secret is wrong: xyz"),
        ):
            resp = client.get("/api/embed-token")

        assert resp.status_code == 503
        body = resp.get_json()
        assert "xyz" not in body["error"]


class TestIndexRoute:
    def test_shows_report_container_when_configured(self, _no_readings):
        app = _make_app(embed_configured=True)
        client = app.test_client()

        resp = client.get("/")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="report-container"' in html
        assert "powerbi.min.js" in html

    def test_shows_placeholder_when_not_configured(self, _no_readings):
        app = _make_app(embed_configured=False)
        client = app.test_client()

        resp = client.get("/")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="report-container"' not in html
        assert "report-pending" in html
        assert "powerbi.min.js" not in html

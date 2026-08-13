"""Power BI "Embed for your customers" service-principal client.

Two-step flow: an AAD client-credentials token authenticates the service
principal, then that token is used to mint a view-only embed token scoped to
one report. Both are cached module-level and shared across every visitor —
the report is public with no RLS, so one embed token serves everyone until it
nears expiry. Never logs the client secret, the AAD token, or the embed token.
"""

import re
from datetime import datetime, timedelta, timezone

import requests

TIMEOUT_SECONDS = 10

AAD_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
AAD_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
REPORT_URL = "https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/reports/{report_id}"
GENERATE_TOKEN_URL = "https://api.powerbi.com/v1.0/myorg/GenerateToken"

# Refresh this long before actual expiry so a slow request never hands out a
# token that dies mid-flight.
REFRESH_MARGIN = timedelta(minutes=5)

_aad_cache = None  # {"token": str, "expires_at": datetime}
_embed_cache = None  # {"token": str, "embedUrl": str, "reportId": str, "expires_at": datetime}


class EmbedError(RuntimeError):
    """Raised when an AAD or Power BI API call fails or returns a non-2xx status."""


def _reset_cache():
    """Test-only hook: clear both module-level caches."""
    global _aad_cache, _embed_cache
    _aad_cache = None
    _embed_cache = None


def _fail_detail(resp):
    """Upstream error *codes* only (AADSTS12345, PowerBINotAuthorizedException,
    ...) — safe to log, never tokens, secrets, or full bodies."""
    codes = sorted(set(re.findall(r"AADSTS\d+", getattr(resp, "text", "")[:2000])))
    if codes:
        return f" ({', '.join(codes)})"
    try:
        code = resp.json().get("error")
        if isinstance(code, dict):
            code = code.get("code")
        if isinstance(code, str) and code:
            return f" ({code})"
    except (ValueError, AttributeError):
        pass
    return ""


def _post(url, step, **kwargs):
    try:
        resp = requests.post(url, timeout=TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        raise EmbedError(f"{step} failed: {exc}") from exc
    if not (200 <= resp.status_code < 300):
        raise EmbedError(
            f"{step} failed with HTTP {resp.status_code}{_fail_detail(resp)}"
        )
    return resp


def _get(url, step, **kwargs):
    try:
        resp = requests.get(url, timeout=TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        raise EmbedError(f"{step} failed: {exc}") from exc
    if not (200 <= resp.status_code < 300):
        raise EmbedError(
            f"{step} failed with HTTP {resp.status_code}{_fail_detail(resp)}"
        )
    return resp


def get_aad_token(cfg):
    """Return a cached AAD access token, refreshing ~5 min before it expires."""
    global _aad_cache

    now = datetime.now(timezone.utc)
    if _aad_cache and now < _aad_cache["expires_at"] - REFRESH_MARGIN:
        return _aad_cache["token"]

    resp = _post(
        AAD_TOKEN_URL.format(tenant=cfg.POWERBI_TENANT_ID),
        "AAD token request",
        data={
            "grant_type": "client_credentials",
            "client_id": cfg.POWERBI_CLIENT_ID,
            "client_secret": cfg.POWERBI_CLIENT_SECRET,
            "scope": AAD_SCOPE,
        },
    )

    try:
        body = resp.json()
        token = body["access_token"]
        expires_in = int(body["expires_in"])
    except (ValueError, KeyError, TypeError) as exc:
        raise EmbedError("malformed AAD token response") from exc

    _aad_cache = {
        "token": token,
        "expires_at": now + timedelta(seconds=expires_in),
    }
    return token


def get_embed_token(cfg):
    """Return a cached {token, embedUrl, reportId, expiresAt} dict for the configured report.

    Refreshes ~5 min before the embed token's own expiration. The dataset id
    needed for GenerateToken is read from the report GET response, never
    hardcoded.
    """
    global _embed_cache

    now = datetime.now(timezone.utc)
    if _embed_cache and now < _embed_cache["expires_at"] - REFRESH_MARGIN:
        return _embed_cache["public"]

    aad_token = get_aad_token(cfg)
    headers = {"Authorization": f"Bearer {aad_token}"}

    report_resp = _get(
        REPORT_URL.format(
            workspace_id=cfg.POWERBI_WORKSPACE_ID, report_id=cfg.POWERBI_REPORT_ID
        ),
        "report fetch",
        headers=headers,
    )
    try:
        report_body = report_resp.json()
        embed_url = report_body["embedUrl"]
        dataset_id = report_body["datasetId"]
    except (ValueError, KeyError, TypeError) as exc:
        raise EmbedError("malformed report response") from exc

    token_resp = _post(
        GENERATE_TOKEN_URL,
        "GenerateToken",
        headers=headers,
        json={
            "reports": [{"id": cfg.POWERBI_REPORT_ID}],
            "datasets": [{"id": dataset_id}],
            "targetWorkspaces": [{"id": cfg.POWERBI_WORKSPACE_ID}],
        },
    )
    try:
        token_body = token_resp.json()
        embed_token = token_body["token"]
        expiration = token_body["expiration"]
    except (ValueError, KeyError, TypeError) as exc:
        raise EmbedError("malformed GenerateToken response") from exc

    try:
        expires_at = datetime.fromisoformat(expiration)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise EmbedError("malformed GenerateToken expiration") from exc

    public = {
        "token": embed_token,
        "embedUrl": embed_url,
        "reportId": cfg.POWERBI_REPORT_ID,
        "expiresAt": expires_at.isoformat(),
    }
    _embed_cache = {"public": public, "expires_at": expires_at}
    return public

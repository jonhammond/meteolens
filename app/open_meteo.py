"""HTTP client for the Open-Meteo `current` forecast and air-quality blocks.

Fetches the 9 weather measurement fields plus 3 air-quality fields
ingest.py needs, for all locations in one batched request per API. Kept
free of Supabase/Flask concerns so it can be tested in isolation.
"""

import time
from datetime import datetime, timezone

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# Paid API subscriptions use a dedicated host with per-key (not per-IP) quotas.
CUSTOMER_FORECAST_URL = "https://customer-api.open-meteo.com/v1/forecast"

# Air quality lives on a separate Open-Meteo service, not the forecast host.
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
CUSTOMER_AIR_QUALITY_URL = "https://customer-air-quality-api.open-meteo.com/v1/air-quality"

# Open-Meteo asks fair-use API consumers to identify themselves.
USER_AGENT = "MeteoLens/1.0 (+https://meteolens.jonhammond.org)"

CURRENT_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "cloud_cover",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
)

AQ_CURRENT_FIELDS = ("us_aqi", "pm10", "pm2_5")

TIMEOUT_SECONDS = 10
RETRY_BACKOFF_SECONDS = 1.5


class OpenMeteoError(RuntimeError):
    """Raised when a forecast fetch ultimately fails (network or non-2xx)."""


def _current_to_reading(current):
    """Convert one `current` block into our reading dict shape."""
    # timeformat=unixtime + timezone=auto still returns a true UTC epoch;
    # do NOT add utc_offset_seconds, that would double-apply the offset.
    recorded_at = datetime.fromtimestamp(current["time"], tz=timezone.utc)

    result = {field: current.get(field) for field in CURRENT_FIELDS}
    result["recorded_at"] = recorded_at
    return result


def _fetch_current_blocks(url, params, coords):
    """Shared request/retry/normalize core for batched `current=` fetches.

    Issues the GET, retries exactly once on 5xx/connection errors for the
    whole batch (4xx responses are not retried since a bad request won't fix
    itself), and normalizes Open-Meteo's N=1-object vs N>1-array response
    shape into a list of `current` blocks, one per input coord, in input
    order. Raises OpenMeteoError on any failure. Callers own everything
    request-shape-specific (URL, params, per-block field extraction).
    """
    headers = {"User-Agent": USER_AGENT}

    attempts = 2
    last_error = None
    for attempt in range(attempts):
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(RETRY_BACKOFF_SECONDS)
                continue
            raise OpenMeteoError(f"request failed: {exc}") from exc

        if resp.status_code >= 500:
            last_error = f"HTTP {resp.status_code}"
            if attempt < attempts - 1:
                time.sleep(RETRY_BACKOFF_SECONDS)
                continue
            raise OpenMeteoError(f"server error after retry: {last_error}")

        if resp.status_code >= 400:
            # Client error: retrying won't help (bad lat/lon, etc).
            raise OpenMeteoError(f"client error {resp.status_code}: {resp.text[:200]}")

        try:
            payload = resp.json()
            # N=1 stays a single response object; N>1 comes back as a JSON
            # array of per-location response objects (same order as the
            # input lat/lon lists), each with its own "current" block.
            # Normalize to a list of `current` blocks either way so callers
            # always get one entry per input coord.
            if isinstance(payload, list):
                current_list = [entry["current"] for entry in payload]
            else:
                current_list = [payload["current"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise OpenMeteoError(f"unexpected response shape: {exc}") from exc

        if len(current_list) != len(coords):
            raise OpenMeteoError(
                f"expected {len(coords)} results, got {len(current_list)}"
            )

        return current_list

    raise OpenMeteoError(f"request failed after retry: {last_error}")


def fetch_current_batch(coords, api_key=None):
    """Fetch the current-conditions block for every (latitude, longitude) pair.

    With `api_key` set, requests go to the customer host and are metered
    against the key's own quota instead of the source IP's — required on
    Render, whose shared egress IPs exhaust the free per-IP quota.

    Open-Meteo's forecast endpoint accepts comma-separated coordinate lists:
    with N>1 pairs the top-level response payload is a JSON array of full
    per-location response objects (each with its own `current` block) in
    input order; with N=1 it stays a single object. This handles both shapes
    and always returns a list, in input order.

    Returns a list of dicts, each with the 9 measurement fields plus
    `recorded_at` (a timezone-aware UTC datetime derived from `current.time`).
    Retries exactly once on 5xx/connection errors for the whole batch; 4xx
    responses are not retried since a bad request won't fix itself.
    """
    params = {
        "latitude": ",".join(str(lat) for lat, _lon in coords),
        "longitude": ",".join(str(lon) for _lat, lon in coords),
        "current": ",".join(CURRENT_FIELDS),
        "timezone": "auto",
        "timeformat": "unixtime",
    }
    url = FORECAST_URL
    if api_key:
        url = CUSTOMER_FORECAST_URL
        params["apikey"] = api_key

    current_list = _fetch_current_blocks(url, params, coords)
    return [_current_to_reading(entry) for entry in current_list]


def fetch_air_quality_batch(coords, api_key=None):
    """Fetch us_aqi/pm10/pm2_5 for every (latitude, longitude) pair.

    Same batching, host-selection, and retry semantics as
    fetch_current_batch, against the separate air-quality API. The AQ
    block's own `time` is deliberately discarded — callers stamp these
    values onto the weather reading's `recorded_at` instead, since AQ and
    weather are fetched as two independent requests.

    Returns a list of dicts, each with exactly AQ_CURRENT_FIELDS, in input
    coord order.
    """
    params = {
        "latitude": ",".join(str(lat) for lat, _lon in coords),
        "longitude": ",".join(str(lon) for _lat, lon in coords),
        "current": ",".join(AQ_CURRENT_FIELDS),
        "timezone": "auto",
        "timeformat": "unixtime",
    }
    url = AIR_QUALITY_URL
    if api_key:
        url = CUSTOMER_AIR_QUALITY_URL
        params["apikey"] = api_key

    current_list = _fetch_current_blocks(url, params, coords)
    return [
        {field: entry.get(field) for field in AQ_CURRENT_FIELDS}
        for entry in current_list
    ]

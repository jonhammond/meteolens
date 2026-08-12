"""HTTP client for the Open-Meteo `current` forecast block.

Fetches the 8 measurement fields ingest.py needs, for all locations in one
batched request. Kept free of Supabase/Flask concerns so it can be tested
in isolation.
"""

import time
from datetime import datetime, timezone

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo asks fair-use API consumers to identify themselves.
USER_AGENT = "MeteoLens/1.0 (+https://meteolens.jonhammond.org)"

CURRENT_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "cloud_cover",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
)

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


def fetch_current_batch(coords):
    """Fetch the current-conditions block for every (latitude, longitude) pair.

    Open-Meteo's forecast endpoint accepts comma-separated coordinate lists:
    with N>1 pairs the top-level response payload is a JSON array of full
    per-location response objects (each with its own `current` block) in
    input order; with N=1 it stays a single object. This handles both shapes
    and always returns a list, in input order.

    Returns a list of dicts, each with the 8 measurement fields plus
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
    headers = {"User-Agent": USER_AGENT}

    attempts = 2
    last_error = None
    for attempt in range(attempts):
        try:
            resp = requests.get(
                FORECAST_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS
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

        return [_current_to_reading(entry) for entry in current_list]

    raise OpenMeteoError(f"request failed after retry: {last_error}")

"""HTTP client for the Open-Meteo `current` forecast block.

Fetches the 8 measurement fields ingest.py needs for one location. Kept
free of Supabase/Flask concerns so it can be tested in isolation.
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


def fetch_current(latitude, longitude):
    """Fetch the current-conditions block for one point.

    Returns a dict with the 8 measurement fields plus `recorded_at` (a
    timezone-aware UTC datetime derived from `current.time`). Retries exactly
    once on 5xx/connection errors; 4xx responses are not retried since a bad
    request won't fix itself.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
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
            current = payload["current"]
        except (ValueError, KeyError) as exc:
            raise OpenMeteoError(f"unexpected response shape: {exc}") from exc

        # timeformat=unixtime + timezone=auto still returns a true UTC epoch;
        # do NOT add utc_offset_seconds, that would double-apply the offset.
        recorded_at = datetime.fromtimestamp(current["time"], tz=timezone.utc)

        result = {field: current.get(field) for field in CURRENT_FIELDS}
        result["recorded_at"] = recorded_at
        return result

    raise OpenMeteoError(f"request failed after retry: {last_error}")

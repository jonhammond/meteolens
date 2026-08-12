"""Backfill missing hourly `weather_readings` rows from Open-Meteo's past-hours API.

The live ingest pipeline (app/ingest.py) fetches Open-Meteo's `current` block
once per hour from Render's egress IP. When that IP gets rate-limited by
Open-Meteo, entire hourly runs are missed and rows are simply absent from
`weather_readings`. This script re-fetches those missing hours from the
local (unblocked) machine's IP using Open-Meteo's `hourly` past-data API and
inserts only the rows that don't already exist, so a run is safe to repeat
and never overwrites live-pipeline data with reconstructed hourly-model
values. Power BI replay is a separate concern handled by
`scripts/backfill_powerbi.py`; this script never touches Power BI.

Usage:
    .venv/bin/python scripts/recover_gap.py --dry-run --since 2026-08-11T00:00:00Z --before 2026-08-11T06:00:00Z
    .venv/bin/python scripts/recover_gap.py --since 2026-08-11T00:00:00Z --before 2026-08-11T06:00:00Z
"""

import argparse
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import os

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.open_meteo import USER_AGENT  # noqa: E402

REQUIRED_VARS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Same measurement fields the live `current`-block pipeline writes (AQ
# excluded — out of scope for gap recovery), fetched here as hourly parallel
# arrays instead.
HOURLY_FIELDS = (
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

TIMEOUT_SECONDS = 10
RETRY_BACKOFF_SECONDS = 1.5

# Open-Meteo's past_hours parameter cap; any --since further back than this
# simply won't be reachable by this endpoint (the window filter below will
# just find no matching hours for the un-coverable part).
MAX_PAST_HOURS = 92

# PostgREST caps a single response at ~1000 rows regardless of .limit(),
# so fetching existing rows must page with .range() rather than trusting one call.
FETCH_PAGE_SIZE = 1000

EXISTING_COLUMNS = "location_id,recorded_at"


class RecoverGapError(RuntimeError):
    """Raised for any fatal recover_gap failure (env, fetch, or insert)."""


def _load_env():
    """Read required env vars from the process environment only.

    Fails fast with every missing name listed, never a hardcoded fallback.
    """
    missing = [name for name in REQUIRED_VARS if not (os.environ.get(name) or "").strip()]
    if missing:
        raise RecoverGapError(
            "Missing required environment variable(s): " + ", ".join(sorted(missing))
        )
    return {name: os.environ[name].strip() for name in REQUIRED_VARS}


class _Cfg:
    """Minimal stand-in for app.config.Config's attribute shape.

    db.build_client() only reads SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY off
    its `cfg` argument, so this avoids instantiating the real Config (which
    also requires INGEST_TOKEN, irrelevant to this offline script).
    """

    def __init__(self, env):
        self.SUPABASE_URL = env["SUPABASE_URL"]
        self.SUPABASE_SERVICE_ROLE_KEY = env["SUPABASE_SERVICE_ROLE_KEY"]


def _parse_iso8601(value, flag_name):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoverGapError(f"invalid {flag_name} value {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _compute_past_hours(since):
    """Hours between `since` and now, rounded up plus 2 for margin, capped at MAX_PAST_HOURS."""
    now = datetime.now(timezone.utc)
    delta_hours = (now - since).total_seconds() / 3600.0
    hours = math.ceil(delta_hours) + 2 if delta_hours > 0 else 2
    return min(max(hours, 0), MAX_PAST_HOURS)


def fetch_hourly_batch(coords, past_hours):
    """Fetch past hourly data for every (latitude, longitude) pair in one request.

    Mirrors app.open_meteo.fetch_current_batch's request/retry pattern, but
    against the `hourly` block instead of `current`: N>1 coords return a
    top-level JSON array of per-location response objects in input order;
    N=1 stays a single object. `forecast_hours=0` excludes future/current-
    partial hours so every returned hour is a completed past hour.

    Returns a list (input order) of per-location `hourly` dicts, each with
    a `time` array of true-UTC unix epochs plus one array per HOURLY_FIELDS
    entry. Retries exactly once on 5xx/connection errors for the whole
    batch; 4xx responses are not retried since a bad request won't fix itself.
    """
    params = {
        "latitude": ",".join(str(lat) for lat, _lon in coords),
        "longitude": ",".join(str(lon) for _lat, lon in coords),
        "hourly": ",".join(HOURLY_FIELDS),
        "past_hours": past_hours,
        "forecast_hours": 0,
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
            raise RecoverGapError(f"request failed: {exc}") from exc

        if resp.status_code >= 500:
            last_error = f"HTTP {resp.status_code}"
            if attempt < attempts - 1:
                time.sleep(RETRY_BACKOFF_SECONDS)
                continue
            raise RecoverGapError(f"server error after retry: {last_error}")

        if resp.status_code >= 400:
            # Client error: retrying won't help (bad lat/lon, etc).
            raise RecoverGapError(f"client error {resp.status_code}: {resp.text[:200]}")

        try:
            payload = resp.json()
            # N=1 stays a single response object; N>1 comes back as a JSON
            # array of per-location response objects (same order as the
            # input lat/lon lists), each with its own "hourly" block.
            if isinstance(payload, list):
                hourly_list = [entry["hourly"] for entry in payload]
            else:
                hourly_list = [payload["hourly"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise RecoverGapError(f"unexpected response shape: {exc}") from exc

        if len(hourly_list) != len(coords):
            raise RecoverGapError(
                f"expected {len(coords)} results, got {len(hourly_list)}"
            )

        return hourly_list

    raise RecoverGapError(f"request failed after retry: {last_error}")


def build_candidates(locations, hourly_list, since, before):
    """Build candidate reading rows for each location's hours within [since, before).

    Returns a dict location_id -> list of reading dicts, each shaped exactly
    like the live pipeline's (the 8 measurement fields + `recorded_at` as a
    timezone-aware UTC datetime), one per in-window hourly index.
    """
    candidates = {}
    for location, hourly in zip(locations, hourly_list):
        times = hourly.get("time", [])
        rows = []
        for idx, epoch in enumerate(times):
            # timeformat=unixtime + timezone=auto still returns a true UTC
            # epoch; do NOT add utc_offset_seconds, that would double-apply
            # the offset (same caveat as app.open_meteo._current_to_reading).
            recorded_at = datetime.fromtimestamp(epoch, tz=timezone.utc)
            if not (since <= recorded_at < before):
                continue
            row = {field: hourly[field][idx] for field in HOURLY_FIELDS}
            row["recorded_at"] = recorded_at
            row["location_id"] = location["id"]
            rows.append(row)
        candidates[location["id"]] = rows
    return candidates


def fetch_existing_keys(client, since, before):
    """(location_id, recorded_at-datetime) pairs already present in [since, before).

    Explicit pagination via `.range()`: PostgREST silently caps a single
    response at ~1000 rows, so this loops until a page comes back short.
    """
    keys = set()
    offset = 0
    while True:
        query = (
            client.table("weather_readings")
            .select(EXISTING_COLUMNS)
            .gte("recorded_at", since.isoformat())
            .lt("recorded_at", before.isoformat())
            .order("recorded_at", desc=False)
        )
        resp = query.range(offset, offset + FETCH_PAGE_SIZE - 1).execute()
        page = resp.data
        for row in page:
            existing_at = _parse_iso8601(row["recorded_at"], "recorded_at")
            keys.add((row["location_id"], existing_at))
        if len(page) < FETCH_PAGE_SIZE:
            break
        offset += FETCH_PAGE_SIZE
    return keys


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Backfill missing hourly weather_readings rows from Open-Meteo past-hours data."
    )
    parser.add_argument(
        "--since",
        required=True,
        help="ISO8601 inclusive lower bound on recorded_at (e.g. 2026-08-11T00:00:00Z)",
    )
    parser.add_argument(
        "--before",
        required=True,
        help="ISO8601 exclusive upper bound on recorded_at",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch + plan only; print per-location missing hours, never write",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        env = _load_env()
        since = _parse_iso8601(args.since, "--since")
        before = _parse_iso8601(args.before, "--before")
    except RecoverGapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if before <= since:
        print("error: --before must be after --since", file=sys.stderr)
        return 1

    client = db.build_client(_Cfg(env))

    try:
        locations = db.fetch_active_locations(client)
    except Exception as exc:  # noqa: BLE001 - surface any fetch failure as a hard error
        print(f"error: failed to fetch active locations: {exc}", file=sys.stderr)
        return 1

    if not locations:
        print("0 locations: nothing to recover")
        return 0

    coords = [(location["latitude"], location["longitude"]) for location in locations]
    past_hours = _compute_past_hours(since)

    try:
        hourly_list = fetch_hourly_batch(coords, past_hours)
    except RecoverGapError as exc:
        print(f"error: failed to fetch from Open-Meteo: {exc}", file=sys.stderr)
        return 1

    candidates = build_candidates(locations, hourly_list, since, before)

    try:
        existing_keys = fetch_existing_keys(client, since, before)
    except Exception as exc:  # noqa: BLE001 - surface any fetch failure as a hard error
        print(f"error: failed to fetch existing readings from Supabase: {exc}", file=sys.stderr)
        return 1

    total_inserted = 0
    total_skipped = 0
    codes_seen = set()

    for location in locations:
        name = location["name"]
        rows = candidates.get(location["id"], [])
        to_insert = []
        skipped = 0
        for row in rows:
            key = (row["location_id"], row["recorded_at"])
            if key in existing_keys:
                skipped += 1
                continue
            to_insert.append(row)

        total_skipped += skipped

        if args.dry_run:
            missing_hours = ", ".join(r["recorded_at"].isoformat() for r in to_insert)
            print(
                f"{name}: {len(to_insert)} missing hour(s) would be inserted, "
                f"{skipped} already present"
                + (f" -- missing: {missing_hours}" if to_insert else "")
            )
            continue

        for row in to_insert:
            code = row["weather_code"]
            if code is not None and code not in codes_seen:
                db.ensure_weather_code(client, code)
                codes_seen.add(code)
            db.upsert_reading(client, row)

        total_inserted += len(to_insert)
        print(f"{name}: inserted {len(to_insert)}, skipped {skipped} (already present)")

    if args.dry_run:
        planned_total = sum(
            len(
                [
                    r
                    for r in candidates.get(loc["id"], [])
                    if (r["location_id"], r["recorded_at"]) not in existing_keys
                ]
            )
            for loc in locations
        )
        print(f"dry-run: {planned_total} would be inserted, {total_skipped} skipped as existing")
        return 0

    print(f"done: {total_inserted} inserted total, {total_skipped} skipped as existing")
    return 0


if __name__ == "__main__":
    sys.exit(main())

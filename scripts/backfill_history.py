"""Backfill historical hourly weather + air-quality into Supabase.

The live ingest pipeline only ever fetches Open-Meteo's `current` block, so
weather_readings has no history before the day this project started polling.
This script exists to fill that gap by pulling Open-Meteo's `archive` (and
matching air-quality archive) hourly endpoints for an explicit date range,
across every known location, and bulk-upserting the joined result via
`app.db.upsert_readings` so re-running the same window is idempotent.

Usage:
    .venv/bin/python scripts/backfill_history.py --start 2026-01-01 --end 2026-01-31 --dry-run
    .venv/bin/python scripts/backfill_history.py --start 2026-01-01 --end 2026-01-31
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.open_meteo import (  # noqa: E402
    AQ_CURRENT_FIELDS,
    CURRENT_FIELDS,
    USER_AGENT,
)

import requests  # noqa: E402

REQUIRED_VARS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")

WEATHER_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CUSTOMER_WEATHER_ARCHIVE_URL = "https://customer-archive-api.open-meteo.com/v1/archive"

AQ_ARCHIVE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
CUSTOMER_AQ_ARCHIVE_URL = "https://customer-air-quality-api.open-meteo.com/v1/air-quality"

TIMEOUT_SECONDS = 30

DEFAULT_CHUNK_SIZE = 500

READING_FIELDS = CURRENT_FIELDS + AQ_CURRENT_FIELDS


class BackfillError(RuntimeError):
    """Raised for any fatal backfill failure (env, fetch, or build)."""


def _load_env():
    """Read required env vars from the process environment only.

    Fails fast with every missing name listed, never a hardcoded fallback.
    OPEN_METEO_API_KEY is optional: when unset, requests go to the free
    (non-customer) archive hosts.
    """
    missing = [name for name in REQUIRED_VARS if not (os.environ.get(name) or "").strip()]
    if missing:
        raise BackfillError(
            "Missing required environment variable(s): " + ", ".join(sorted(missing))
        )
    env = {name: os.environ[name].strip() for name in REQUIRED_VARS}
    api_key = (os.environ.get("OPEN_METEO_API_KEY") or "").strip()
    env["OPEN_METEO_API_KEY"] = api_key or None
    return env


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
        raise BackfillError(f"invalid {flag_name} value {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def fetch_all_locations(client):
    """Every location (active or not), as a list of id/name/latitude/longitude dicts.

    Backfill must cover history from locations later deactivated, unlike the
    live ingest path which only reads active ones, so this doesn't filter on
    is_active.
    """
    resp = client.table("locations").select("id,name,latitude,longitude").execute()
    return resp.data


def _fetch_archive(url, customer_url, fields, coords, start, end, api_key):
    """Shared request/normalize core for batched archive `hourly=` fetches.

    Normalizes Open-Meteo's N=1-object vs N>1-array response shape into a
    list of `hourly` blocks, one per input coord, in input order. Raises
    BackfillError on any failure.
    """
    params = {
        "latitude": ",".join(str(lat) for lat, _lon in coords),
        "longitude": ",".join(str(lon) for _lat, lon in coords),
        "hourly": ",".join(fields),
        "timezone": "auto",
        "timeformat": "unixtime",
        "start_date": start,
        "end_date": end,
    }
    target_url = url
    if api_key:
        target_url = customer_url
        params["apikey"] = api_key

    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(target_url, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise BackfillError(f"request to {target_url} failed: {exc}") from exc

    if resp.status_code >= 400:
        raise BackfillError(
            f"request to {target_url} failed with HTTP {resp.status_code}: {resp.text[:200]}"
        )

    try:
        payload = resp.json()
        if isinstance(payload, list):
            hourly_list = [entry["hourly"] for entry in payload]
        else:
            hourly_list = [payload["hourly"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise BackfillError(f"unexpected response shape from {target_url}: {exc}") from exc

    if len(hourly_list) != len(coords):
        raise BackfillError(
            f"expected {len(coords)} results from {target_url}, got {len(hourly_list)}"
        )

    return hourly_list


def fetch_weather_archive(coords, start, end, api_key=None):
    """Fetch hourly weather archive fields for every (latitude, longitude) pair.

    Returns a list of `hourly` blocks (dict of parallel arrays keyed by field
    name, plus "time"), one per input coord, in input order.
    """
    return _fetch_archive(
        WEATHER_ARCHIVE_URL, CUSTOMER_WEATHER_ARCHIVE_URL, CURRENT_FIELDS, coords, start, end, api_key
    )


def fetch_aq_archive(coords, start, end, api_key=None):
    """Fetch hourly air-quality archive fields for every (latitude, longitude) pair.

    Returns a list of `hourly` blocks, one per input coord, in input order.
    """
    return _fetch_archive(
        AQ_ARCHIVE_URL, CUSTOMER_AQ_ARCHIVE_URL, AQ_CURRENT_FIELDS, coords, start, end, api_key
    )


def _hourly_block_to_series(hourly):
    """Convert one `hourly` response block into a list of (datetime, {field: value}).

    `time` is a true UTC epoch (timeformat=unixtime + timezone=auto); do NOT
    add utc_offset_seconds, that would double-apply the offset.
    """
    times = hourly.get("time") or []
    series = []
    for idx, epoch in enumerate(times):
        recorded_at = datetime.fromtimestamp(epoch, tz=timezone.utc)
        values = {field: hourly.get(field, [None] * len(times))[idx] for field in hourly if field != "time"}
        series.append((recorded_at, values))
    return series


def build_rows(locations, weather_blocks, aq_blocks):
    """Join weather and AQ hourly series by timestamp and build weather_readings rows.

    Joins BY TIMESTAMP rather than by index: the two APIs can return
    different-length series or different start points, so AQ values are
    looked up per weather timestamp via a dict keyed on the recorded_at
    datetime, not zipped positionally. A timestamp with no matching AQ entry
    gets all AQ fields set to None.

    Rows where temperature_2m is None are skipped (the archive API pads
    trailing not-yet-observed hours with nulls) and counted separately.

    Returns (rows, skipped_count) where each row is a dict ready for
    db.upsert_readings, with recorded_at already serialized via .isoformat().
    """
    rows = []
    skipped = 0
    for location, weather_hourly, aq_hourly in zip(locations, weather_blocks, aq_blocks):
        aq_series = _hourly_block_to_series(aq_hourly)
        aq_by_time = {recorded_at: values for recorded_at, values in aq_series}

        weather_series = _hourly_block_to_series(weather_hourly)
        for recorded_at, weather_values in weather_series:
            if weather_values.get("temperature_2m") is None:
                skipped += 1
                continue
            aq_values = aq_by_time.get(recorded_at, {})
            row = {"location_id": location["id"], "recorded_at": recorded_at.isoformat()}
            for field in CURRENT_FIELDS:
                row[field] = weather_values.get(field)
            for field in AQ_CURRENT_FIELDS:
                row[field] = aq_values.get(field)
            rows.append(row)
    return rows, skipped


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Backfill historical hourly weather + air-quality into Supabase."
    )
    parser.add_argument("--start", required=True, help="start date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="end date, inclusive (YYYY-MM-DD)")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"max rows per upsert batch (default {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch + build rows only; print counts, time range, and one sample row, never write",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        env = _load_env()
    except BackfillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    client = db.build_client(_Cfg(env))
    api_key = env["OPEN_METEO_API_KEY"]

    try:
        locations = fetch_all_locations(client)
    except Exception as exc:  # noqa: BLE001 - surface any fetch failure as a hard error
        print(f"error: failed to fetch locations from Supabase: {exc}", file=sys.stderr)
        return 1

    if not locations:
        print("0 locations found: nothing to backfill")
        return 0

    print(f"{len(locations)} locations found")

    coords = [(loc["latitude"], loc["longitude"]) for loc in locations]

    try:
        weather_blocks = fetch_weather_archive(coords, args.start, args.end, api_key=api_key)
        print(f"weather archive: fetched hourly data for {len(weather_blocks)} locations")
        aq_blocks = fetch_aq_archive(coords, args.start, args.end, api_key=api_key)
        print(f"air-quality archive: fetched hourly data for {len(aq_blocks)} locations")
    except BackfillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rows, skipped = build_rows(locations, weather_blocks, aq_blocks)
    print(f"rows built: {len(rows)} (skipped {skipped} with null temperature_2m)")

    if not rows:
        print("0 rows to write")
        return 0

    recorded_ats = [row["recorded_at"] for row in rows]
    range_start = min(recorded_ats)
    range_end = max(recorded_ats)
    distinct_locations = len({row["location_id"] for row in rows})

    if args.dry_run:
        print(f"distinct locations with rows: {distinct_locations}")
        print(f"time range: {range_start} to {range_end}")
        print("sample row:", rows[0])
        return 0

    distinct_codes = {row["weather_code"] for row in rows if row["weather_code"] is not None}
    for code in distinct_codes:
        db.ensure_weather_code(client, code)
    print(f"weather codes ensured: {len(distinct_codes)} distinct")

    upserted = db.upsert_readings(client, rows, chunk_size=args.chunk_size)
    print(
        f"done: upserted {upserted} rows, {distinct_locations} locations, "
        f"time range {range_start} to {range_end}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

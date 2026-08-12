"""Replay Supabase weather-reading history into a fresh Power BI push dataset.

Power BI push datasets have no upsert/dedupe: every POST appends rows. This
script exists to (re)populate a newly (re)created dataset from Supabase, the
system of record, using the identical row-enrichment logic the live hourly
ingest pipeline uses (`app.ingest.build_pbi_row`), including the exact
`recorded_at` string format. Use `--since`/`--before` to bound the replay to
a window the live pipeline hasn't already pushed, so rows aren't duplicated
in the dataset.

Usage:
    .venv/bin/python scripts/backfill_powerbi.py --dry-run
    .venv/bin/python scripts/backfill_powerbi.py --since 2026-01-01T00:00:00Z
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.ingest import build_pbi_row  # noqa: E402
from app.powerbi import PowerBIError, push_rows  # noqa: E402

REQUIRED_VARS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "POWERBI_PUSH_URL")

# PostgREST caps a single response at ~1000 rows regardless of .limit(),
# so fetching must page with .range() rather than trusting one call.
FETCH_PAGE_SIZE = 1000

DEFAULT_BATCH_SIZE = 10000
PUSH_RATE_LIMIT_SECONDS = 1.0

READING_COLUMNS = (
    "location_id,recorded_at,temperature_2m,apparent_temperature,"
    "relative_humidity_2m,precipitation,cloud_cover,weather_code,"
    "wind_speed_10m,wind_gusts_10m,dew_point_2m,us_aqi,pm10,pm2_5"
)


class BackfillError(RuntimeError):
    """Raised for any fatal backfill failure (env, fetch, or push)."""


def _load_env():
    """Read required env vars from the process environment only.

    Fails fast with every missing name listed, never a hardcoded fallback.
    """
    missing = [name for name in REQUIRED_VARS if not (os.environ.get(name) or "").strip()]
    if missing:
        raise BackfillError(
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
        raise BackfillError(f"invalid {flag_name} value {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def fetch_all_locations(client):
    """Every location (active or not), id -> name.

    Backfill must cover history from locations later deactivated, unlike the
    live ingest path which only reads active ones.
    """
    resp = client.table("locations").select("id,name").execute()
    return {loc["id"]: loc["name"] for loc in resp.data}


def fetch_readings(client, since=None, before=None):
    """All weather_readings rows in [since, before), oldest first.

    Explicit pagination via `.range()`: PostgREST silently caps a single
    response at ~1000 rows, so this loops until a page comes back short.
    """
    rows = []
    offset = 0
    while True:
        query = client.table("weather_readings").select(READING_COLUMNS)
        if since is not None:
            query = query.gte("recorded_at", since.isoformat())
        if before is not None:
            query = query.lt("recorded_at", before.isoformat())
        query = query.order("recorded_at", desc=False)
        resp = query.range(offset, offset + FETCH_PAGE_SIZE - 1).execute()
        page = resp.data
        rows.extend(page)
        if len(page) < FETCH_PAGE_SIZE:
            break
        offset += FETCH_PAGE_SIZE
    return rows


def enrich_readings(readings, locations_by_id):
    """Build Power BI rows byte-identical to the live ingest pipeline's output.

    Readings come back from PostgREST with `recorded_at` as an ISO string;
    `build_pbi_row` expects a datetime (it calls `.isoformat()` on it), so
    each row is parsed back to a datetime before enrichment to reproduce the
    exact same serialization the live path produces.
    """
    pbi_rows = []
    for reading in readings:
        location_name = locations_by_id.get(reading["location_id"])
        if location_name is None:
            continue
        reading = dict(reading)
        reading["recorded_at"] = _parse_iso8601(reading["recorded_at"], "recorded_at")
        pbi_rows.append(build_pbi_row(location_name, reading))
    return pbi_rows


def push_in_batches(push_url, rows, batch_size):
    """POST `rows` to Power BI in batches of at most `batch_size`.

    Sleeps at least 1s between batches to respect the push API's ~1 req/sec
    limit. Prints per-batch progress; propagates the first PowerBIError.
    """
    total_batches = (len(rows) + batch_size - 1) // batch_size
    for batch_num in range(total_batches):
        start = batch_num * batch_size
        batch = rows[start : start + batch_size]
        push_rows(push_url, batch)
        print(f"batch {batch_num + 1}/{total_batches}: pushed {len(batch)} rows")
        if batch_num < total_batches - 1:
            time.sleep(PUSH_RATE_LIMIT_SECONDS)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Replay Supabase weather-reading history into Power BI."
    )
    parser.add_argument(
        "--since", help="ISO8601 inclusive lower bound on recorded_at (e.g. 2026-01-01T00:00:00Z)"
    )
    parser.add_argument(
        "--before", help="ISO8601 exclusive upper bound on recorded_at"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"max rows per push request (default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--only-minute",
        type=int,
        help=(
            "push only readings whose recorded_at minute equals this value. "
            "Archive-backfilled rows land on :00 while live-pipeline rows carry "
            "Open-Meteo's 15-minute observation stamps, so --only-minute 0 "
            "replays just the backfill into a dataset that already holds the "
            "live rows, without duplicating them (push datasets cannot dedupe)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch + enrich only; print row count, time range, and one sample row, never POST",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        env = _load_env()
        since = _parse_iso8601(args.since, "--since") if args.since else None
        before = _parse_iso8601(args.before, "--before") if args.before else None
    except BackfillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    client = db.build_client(_Cfg(env))

    try:
        locations_by_id = fetch_all_locations(client)
        readings = fetch_readings(client, since=since, before=before)
    except Exception as exc:  # noqa: BLE001 - surface any fetch failure as a hard error
        print(f"error: failed to fetch from Supabase: {exc}", file=sys.stderr)
        return 1

    if args.only_minute is not None:
        total_before = len(readings)
        readings = [
            reading
            for reading in readings
            if _parse_iso8601(reading["recorded_at"], "recorded_at").minute
            == args.only_minute
        ]
        print(
            f"--only-minute {args.only_minute}: kept {len(readings)} of "
            f"{total_before} readings"
        )

    if not readings:
        print("0 rows: no readings found in the given window")
        return 0

    pbi_rows = enrich_readings(readings, locations_by_id)
    range_start = readings[0]["recorded_at"]
    range_end = readings[-1]["recorded_at"]

    if args.dry_run:
        print(f"{len(pbi_rows)} rows, time range {range_start} to {range_end}")
        print("sample row:", pbi_rows[0])
        return 0

    try:
        push_in_batches(env["POWERBI_PUSH_URL"], pbi_rows, args.batch_size)
    except PowerBIError as exc:
        print(f"error: push failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"done: pushed {len(pbi_rows)} rows total, time range {range_start} to {range_end}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

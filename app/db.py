"""Thin wrapper over supabase-py (REST over HTTPS).

Render is IPv4-only, so this deliberately avoids a direct Postgres connection
and goes through PostgREST instead. Verified against the installed
supabase 2.31.0 / postgrest 2.31.0 API: `create_client(url, key)`, and the
sync query builder chain `.table().select().eq().order().limit().execute()`,
plus `.upsert(rows, on_conflict=...)`.
"""

from supabase import create_client

from app.weather_codes import describe, icon

# numeric/smallint columns in weather_readings; used to coerce Open-Meteo's
# measurement dict into JSON-safe values before insert.
NUMERIC_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "dew_point_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "pm10",
    "pm2_5",
    "snow_depth",
)
SMALLINT_FIELDS = (
    "relative_humidity_2m",
    "cloud_cover",
    "weather_code",
    "us_aqi",
    "us_aqi_pm2_5",
    "us_aqi_pm10",
)


def build_client(cfg):
    """Create a supabase-py client authenticated as service_role."""
    return create_client(cfg.SUPABASE_URL, cfg.SUPABASE_SERVICE_ROLE_KEY)


def fetch_active_locations(client):
    """Active locations ordered by name, as list of dicts."""
    resp = (
        client.table("locations")
        .select("id,name,latitude,longitude")
        .eq("is_active", True)
        .order("name")
        .execute()
    )
    return resp.data


def ensure_weather_code(client, code):
    """Insert `code` into weather_codes if it isn't already there.

    Open-Meteo can in principle return a WMO code we haven't seeded; without
    this the FK on weather_readings.weather_code would reject the insert.
    Upsert with ignore_duplicates makes repeated calls for the same code free.
    """
    if code is None:
        return
    client.table("weather_codes").upsert(
        {"code": code, "description": describe(code)},
        on_conflict="code",
        ignore_duplicates=True,
    ).execute()


def _coerce_reading(row):
    row = dict(row)
    for field in NUMERIC_FIELDS:
        if field in row and row[field] is not None:
            row[field] = float(row[field])
    for field in SMALLINT_FIELDS:
        if field in row and row[field] is not None:
            row[field] = int(row[field])
    if isinstance(row.get("recorded_at"), object) and hasattr(row["recorded_at"], "isoformat"):
        row["recorded_at"] = row["recorded_at"].isoformat()
    return row


def upsert_reading(client, row):
    """Upsert one weather_readings row.

    on_conflict targets the (location_id, recorded_at) unique constraint so
    re-running ingest for the same hour updates in place instead of
    duplicating rows.
    """
    payload = _coerce_reading(row)
    client.table("weather_readings").upsert(
        payload, on_conflict="location_id,recorded_at"
    ).execute()


def upsert_readings(client, rows, chunk_size=500):
    """Upsert many weather_readings rows in chunks.

    Mirrors upsert_reading's on_conflict target (location_id,recorded_at) so
    re-running a backfill for the same hours updates in place instead of
    duplicating rows. Rows are chunked because PostgREST/the underlying HTTP
    request has practical payload-size limits; sending thousands of backfill
    rows in one call risks hitting them, so this splits into `chunk_size`
    batches and issues one `.upsert()` call per batch. No-op on empty input.
    Returns the total number of rows upserted.
    """
    if not rows:
        return 0
    total = 0
    for start in range(0, len(rows), chunk_size):
        batch = rows[start : start + chunk_size]
        payload = [_coerce_reading(row) for row in batch]
        client.table("weather_readings").upsert(
            payload, on_conflict="location_id,recorded_at"
        ).execute()
        total += len(payload)
    return total


def fetch_latest_per_location(client):
    """Newest reading per active location, joined to location name + weather description.

    PostgREST has no lateral-join primitive over the REST API, so this pulls
    all readings for active locations ordered newest-first and keeps the
    first row seen per location_id in Python. Simpler and correct; the
    per-location row count stays small (12 locations, hourly cadence) so the
    extra rows fetched are cheap.
    """
    locations = fetch_active_locations(client)
    if not locations:
        return []
    by_id = {loc["id"]: loc for loc in locations}

    resp = (
        client.table("weather_readings")
        .select(
            "location_id,recorded_at,temperature_2m,apparent_temperature,"
            "relative_humidity_2m,dew_point_2m,precipitation,cloud_cover,weather_code,"
            "wind_speed_10m,wind_gusts_10m,snow_depth,us_aqi,pm10,pm2_5,us_aqi_pm2_5,us_aqi_pm10,"
            "temperature_2m_f,apparent_temperature_f,dew_point_2m_f,"
            "wind_speed_10m_mph,wind_gusts_10m_mph,precipitation_in,snow_depth_in"
        )
        .in_("location_id", list(by_id.keys()))
        .order("recorded_at", desc=True)
        .execute()
    )

    latest = {}
    for reading in resp.data:
        loc_id = reading["location_id"]
        if loc_id in latest:
            continue
        latest[loc_id] = reading

    results = []
    for loc_id, loc in by_id.items():
        reading = latest.get(loc_id)
        if reading is None:
            continue
        results.append(
            {
                "location": loc["name"],
                "latitude": float(loc["latitude"]) if loc["latitude"] is not None else None,
                "longitude": float(loc["longitude"]) if loc["longitude"] is not None else None,
                "recorded_at": reading["recorded_at"],
                "temperature_2m": reading["temperature_2m"],
                "apparent_temperature": reading["apparent_temperature"],
                "relative_humidity_2m": reading["relative_humidity_2m"],
                "dew_point_2m": reading["dew_point_2m"],
                "precipitation": reading["precipitation"],
                "cloud_cover": reading["cloud_cover"],
                "weather_code": reading["weather_code"],
                "weather_desc": describe(reading["weather_code"]),
                "weather_icon": icon(reading["weather_code"]),
                "wind_speed_10m": reading["wind_speed_10m"],
                "wind_gusts_10m": reading["wind_gusts_10m"],
                "snow_depth": reading["snow_depth"],
                "us_aqi": reading["us_aqi"],
                "pm10": reading["pm10"],
                "pm2_5": reading["pm2_5"],
                "us_aqi_pm2_5": reading["us_aqi_pm2_5"],
                "us_aqi_pm10": reading["us_aqi_pm10"],
                "temperature_2m_f": reading["temperature_2m_f"],
                "apparent_temperature_f": reading["apparent_temperature_f"],
                "dew_point_2m_f": reading["dew_point_2m_f"],
                "wind_speed_10m_mph": reading["wind_speed_10m_mph"],
                "wind_gusts_10m_mph": reading["wind_gusts_10m_mph"],
                "precipitation_in": reading["precipitation_in"],
                "snow_depth_in": reading["snow_depth_in"],
            }
        )
    results.sort(key=lambda r: r["location"])
    return results

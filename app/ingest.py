"""Per-run ingest orchestration: Open-Meteo -> Supabase -> Power BI.

Supabase is the system of record; a Power BI push failure (expected locally,
where POWERBI_PUSH_URL is a placeholder until M7) must never flip an
already-successful Supabase write to "error".
"""

from app import db
from app.open_meteo import OpenMeteoError, fetch_current_batch
from app.powerbi import PowerBIError, push_rows
from app.weather_codes import describe

# --- PBI row enrichment thresholds -----------------------------------------
# Computed server-side in Python: Power BI free has no DAX conditional-format
# authoring path we want to depend on, so color/band fields are precomputed.

# precipitation (mm): any measurable precip gets flagged and colored blue.
PRECIP_THRESHOLD = 0.0
PRECIP_COLOR_NONE = "#cccccc"
PRECIP_COLOR_ACTIVE = "#1f77b4"

# temperature (C): cold / mild / hot bands, red-ish for hot, blue-ish for cold.
TEMP_COLD_MAX = 5.0
TEMP_HOT_MIN = 27.0
TEMP_COLOR_COLD = "#3b82f6"
TEMP_COLOR_MILD = "#22c55e"
TEMP_COLOR_HOT = "#ef4444"
TEMP_BAND_COLD, TEMP_BAND_MILD, TEMP_BAND_HOT = 0, 1, 2

# cloud cover (%): clear / partly / overcast bands.
CLOUD_CLEAR_MAX = 25
CLOUD_PARTLY_MAX = 75
CLOUD_COLOR_CLEAR = "#facc15"
CLOUD_COLOR_PARTLY = "#94a3b8"
CLOUD_COLOR_OVERCAST = "#475569"
CLOUD_BAND_CLEAR, CLOUD_BAND_PARTLY, CLOUD_BAND_OVERCAST = 0, 1, 2

UNKNOWN_COLOR = "#999999"

# Cap on error text landing in the /api/ingest response summary: the cron
# caller enforces a response-size ceiling, and 12 verbose error strings can
# blow past it. Applied to every error string in the summary (per-location
# and the "powerbi" key) so the response stays small no matter how many
# locations fail at once.
ERROR_TEXT_LIMIT = 100


def _short(exc, limit=ERROR_TEXT_LIMIT):
    """Render an exception as a length-capped string for the response summary."""
    text = str(exc)
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


def _precip_enrichment(precip):
    if precip is None:
        return UNKNOWN_COLOR, 0
    if precip > PRECIP_THRESHOLD:
        return PRECIP_COLOR_ACTIVE, 1
    return PRECIP_COLOR_NONE, 0


def _temp_enrichment(temp):
    if temp is None:
        return UNKNOWN_COLOR, -1
    if temp <= TEMP_COLD_MAX:
        return TEMP_COLOR_COLD, TEMP_BAND_COLD
    if temp >= TEMP_HOT_MIN:
        return TEMP_COLOR_HOT, TEMP_BAND_HOT
    return TEMP_COLOR_MILD, TEMP_BAND_MILD


def _cloud_enrichment(cloud):
    if cloud is None:
        return UNKNOWN_COLOR, -1
    if cloud <= CLOUD_CLEAR_MAX:
        return CLOUD_COLOR_CLEAR, CLOUD_BAND_CLEAR
    if cloud <= CLOUD_PARTLY_MAX:
        return CLOUD_COLOR_PARTLY, CLOUD_BAND_PARTLY
    return CLOUD_COLOR_OVERCAST, CLOUD_BAND_OVERCAST


def build_pbi_row(location_name, reading):
    """Enrich one raw reading into the exact row shape pushed to Power BI.

    Pure function shared by the live per-run ingest path and
    `scripts/backfill_powerbi.py`, so a historical replay produces
    byte-identical rows to what the hourly pipeline sends (same
    `recorded_at` string format, same color/band thresholds).
    """
    precip_color, precip_flag = _precip_enrichment(reading["precipitation"])
    temp_color, temp_band = _temp_enrichment(reading["temperature_2m"])
    cloud_color, cloud_band = _cloud_enrichment(reading["cloud_cover"])

    return {
        "recorded_at": reading["recorded_at"].isoformat(),
        "location": location_name,
        "temperature_2m": reading["temperature_2m"],
        "apparent_temperature": reading["apparent_temperature"],
        "relative_humidity_2m": reading["relative_humidity_2m"],
        "precipitation": reading["precipitation"],
        "cloud_cover": reading["cloud_cover"],
        "weather_code": reading["weather_code"],
        "wind_speed_10m": reading["wind_speed_10m"],
        "wind_gusts_10m": reading["wind_gusts_10m"],
        "weather_desc": describe(reading["weather_code"]),
        "precip_color": precip_color,
        "temp_color": temp_color,
        "cloud_color": cloud_color,
        "precip_flag": precip_flag,
        "temp_band": temp_band,
        "cloud_band": cloud_band,
    }


def run_ingest(cfg):
    """Fetch + write current conditions for every active location.

    Returns a summary dict: one key per location name ("ok" or
    "error: <reason>"), plus a "powerbi" key describing the batched push
    outcome. All locations are fetched from Open-Meteo in a single batched
    request (one source-IP call instead of N) so a single location failing
    never aborts the run, but a batch-level fetch failure fails every
    location at once (there's no per-location response to salvage).
    """
    client = db.build_client(cfg)
    summary = {}
    pbi_rows = []

    locations = db.fetch_active_locations(client)
    coords = [(location["latitude"], location["longitude"]) for location in locations]

    try:
        readings = fetch_current_batch(coords) if coords else []
    except OpenMeteoError as exc:
        error_text = f"error: {_short(exc)}"
        for location in locations:
            summary[location["name"]] = error_text
        summary["powerbi"] = "skipped: no rows"
        return summary

    for location, reading in zip(locations, readings):
        name = location["name"]
        try:
            db.ensure_weather_code(client, reading["weather_code"])
            db.upsert_reading(
                client,
                {
                    "location_id": location["id"],
                    "recorded_at": reading["recorded_at"],
                    "temperature_2m": reading["temperature_2m"],
                    "apparent_temperature": reading["apparent_temperature"],
                    "relative_humidity_2m": reading["relative_humidity_2m"],
                    "precipitation": reading["precipitation"],
                    "cloud_cover": reading["cloud_cover"],
                    "weather_code": reading["weather_code"],
                    "wind_speed_10m": reading["wind_speed_10m"],
                    "wind_gusts_10m": reading["wind_gusts_10m"],
                },
            )
            pbi_rows.append(build_pbi_row(name, reading))
            summary[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - one bad location must not abort the run
            summary[name] = f"error: {_short(exc)}"

    try:
        push_rows(cfg.POWERBI_PUSH_URL, pbi_rows)
        summary["powerbi"] = "ok" if pbi_rows else "skipped: no rows"
    except PowerBIError as exc:
        summary["powerbi"] = f"error: {_short(exc)}"

    return summary

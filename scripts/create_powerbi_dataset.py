"""Create a NEW Power BI push-streaming dataset via REST, for testing only.

Purpose: verify whether a PushStreaming dataset created via the REST API (as
opposed to one created interactively in the Power BI service) exposes a
key-based Push URL under its "API Info" / streaming settings. This is a
one-off diagnostic script — it creates a *separate* dataset named
"meteolens-v2" alongside the existing production dataset and never touches
the existing one.

Auth: uses the caller's own Azure AD credentials via `az account
get-access-token`, not a stored app secret. Run `az login` first if this
fails. The token is held in memory only and is never printed, logged, or
written to disk. Response bodies from the Power BI datasets API are safe to
print — they don't carry the push key (only the dedicated
"Push URL"/"API Info" surface in the service ever shows that).

Usage:
    .venv/bin/python scripts/create_powerbi_dataset.py --dry-run
    .venv/bin/python scripts/create_powerbi_dataset.py
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

API_ROOT = "https://api.powerbi.com/v1.0/myorg"
DATASET_NAME = "meteolens-v2"
AAD_RESOURCE = "https://analysis.windows.net/powerbi/api"

REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
DEFAULT_RETRY_AFTER_SECONDS = 5

# --- RealTimeData table schema ----------------------------------------------
# Column names/order MUST mirror app.ingest.build_pbi_row exactly (the same
# function backfill_powerbi.py uses to populate the existing dataset) so a
# real push against this test dataset would carry the identical row shape.
# Types match the existing production dataset: DateTime for recorded_at,
# String for location/weather_desc/*_color fields, Double for everything else.
REALTIME_STRING_COLUMNS = (
    "location",
    "weather_desc",
    "precip_color",
    "temp_color",
    "cloud_color",
    "aqi_color",
)

# Order matches build_pbi_row's return dict (app/ingest.py) field-for-field.
REALTIME_DATA_COLUMNS = (
    "recorded_at",
    "location",
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "cloud_cover",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "weather_desc",
    "precip_color",
    "temp_color",
    "cloud_color",
    "precip_flag",
    "temp_band",
    "cloud_band",
    "dew_point_2m",
    "us_aqi",
    "pm10",
    "pm2_5",
    "aqi_color",
    "aqi_band",
    "temperature_2m_f",
    "apparent_temperature_f",
    "dew_point_2m_f",
    "wind_speed_10m_mph",
    "wind_gusts_10m_mph",
    "precipitation_in",
)

assert len(REALTIME_DATA_COLUMNS) == 29, "RealTimeData must have exactly 29 columns"


def _realtime_column_type(name):
    if name == "recorded_at":
        return "DateTime"
    if name in REALTIME_STRING_COLUMNS:
        return "String"
    return "Double"


# Reference/dimension data pushed once after dataset creation, for relating
# RealTimeData.location -> Cities.city in the report model.
UNIT_ROWS = [{"unit": "Metric"}, {"unit": "Imperial"}]

CITY_ROWS = [
    {"city": "Denver", "latitude": 39.73915, "longitude": -104.98470},
    {"city": "Colorado Springs", "latitude": 38.83388, "longitude": -104.82136},
    {"city": "Pueblo", "latitude": 38.25445, "longitude": -104.60914},
    {"city": "Leadville", "latitude": 39.25082, "longitude": -106.29252},
    {"city": "Fort Collins", "latitude": 40.58526, "longitude": -105.08442},
    {"city": "Durango", "latitude": 37.27528, "longitude": -107.88007},
    {"city": "Grand Junction", "latitude": 39.06387, "longitude": -108.55065},
    {"city": "Glenwood Springs", "latitude": 39.55054, "longitude": -107.32478},
    {"city": "Steamboat Springs", "latitude": 40.48498, "longitude": -106.83172},
    {"city": "Castle Rock", "latitude": 39.37221, "longitude": -104.85609},
    {"city": "Longmont", "latitude": 40.16721, "longitude": -105.10193},
    {"city": "Boulder", "latitude": 40.01499, "longitude": -105.27055},
]


class CreateDatasetError(RuntimeError):
    """Raised for any fatal failure: auth, API error, or unexpected response shape."""


def build_dataset_schema(include_relationships=True):
    """Return the dataset-creation request body as a plain dict.

    Pure/offline: no network, no token required. Used by both the real POST
    and --dry-run so the printed schema is exactly what would be sent.
    """
    tables = [
        {
            "name": "RealTimeData",
            "columns": [
                {"name": name, "dataType": _realtime_column_type(name)}
                for name in REALTIME_DATA_COLUMNS
            ],
        },
        {
            "name": "Units",
            "columns": [{"name": "unit", "dataType": "String"}],
        },
        {
            "name": "Cities",
            "columns": [
                {"name": "city", "dataType": "String"},
                {"name": "latitude", "dataType": "Double"},
                {"name": "longitude", "dataType": "Double"},
            ],
        },
    ]

    body = {
        "name": DATASET_NAME,
        "defaultMode": "PushStreaming",
        "tables": tables,
    }

    if include_relationships:
        body["relationships"] = [
            {
                "name": "CityToReadings",
                "fromTable": "RealTimeData",
                "fromColumn": "location",
                "toTable": "Cities",
                "toColumn": "city",
                "crossFilteringBehavior": "OneDirection",
            }
        ]

    return body


def get_access_token():
    """Obtain a bearer token for the Power BI REST API via the Azure CLI.

    Uses the caller's own `az login` session (no stored app secret). Returns
    the token string; never logs it. Raises CreateDatasetError with a clear
    "run az login" message if the az CLI is missing, not logged in, or
    returns an unexpected shape.
    """
    try:
        proc = subprocess.run(
            [
                "az",
                "account",
                "get-access-token",
                "--resource",
                AAD_RESOURCE,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise CreateDatasetError(
            "the 'az' CLI was not found. Install the Azure CLI, then run "
            "'az login' and retry."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CreateDatasetError("'az account get-access-token' timed out") from exc

    if proc.returncode != 0:
        raise CreateDatasetError(
            "'az account get-access-token' failed - run 'az login' first, "
            "then retry. (az stderr not shown here; rerun the az command "
            "directly in your shell to see details)"
        )

    try:
        payload = json.loads(proc.stdout)
        token = payload["accessToken"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CreateDatasetError(
            "unexpected output from 'az account get-access-token' "
            "(missing accessToken field)"
        ) from exc

    if not token:
        raise CreateDatasetError("'az account get-access-token' returned an empty token")

    return token


def _request(method, url, token, body=None):
    """Issue one HTTP request against the Power BI REST API.

    Retries on 429 up to MAX_RETRIES times, honoring Retry-After when
    present. Returns the parsed JSON body (or None for an empty body).
    Raises CreateDatasetError with status + response body on any other
    non-2xx response, or on a final 429 after retries are exhausted.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and attempt < MAX_RETRIES:
                retry_after = exc.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else DEFAULT_RETRY_AFTER_SECONDS
                except ValueError:
                    delay = DEFAULT_RETRY_AFTER_SECONDS
                print(f"429 rate-limited, retrying in {delay:.0f}s ({attempt}/{MAX_RETRIES})...")
                time.sleep(delay)
                continue
            raise CreateDatasetError(
                f"{method} {url.split('?')[0]} failed: HTTP {exc.code}\n{error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise CreateDatasetError(f"{method} {url.split('?')[0]} failed: {exc}") from exc

    raise CreateDatasetError(f"{method} {url.split('?')[0]} failed: exhausted retries on HTTP 429")


def find_existing_dataset(token, name):
    """Return the existing dataset dict with this name, or None."""
    result = _request("GET", f"{API_ROOT}/datasets", token)
    for dataset in (result or {}).get("value", []):
        if dataset.get("name") == name:
            return dataset
    return None


def create_dataset(token):
    """POST the dataset-creation request; retry once without relationships on a 4xx.

    Returns (dataset_response_dict, relationship_created_bool).
    """
    url = f"{API_ROOT}/datasets?defaultRetentionPolicy=basicFIFO"
    body = build_dataset_schema(include_relationships=True)

    try:
        return _request("POST", url, token, body), True
    except CreateDatasetError as exc:
        message = str(exc)
        if "relationship" not in message.lower():
            raise
        print("warning: dataset creation with relationships failed, retrying without them")
        print(f"  ({message.splitlines()[0]})")
        body_no_rel = build_dataset_schema(include_relationships=False)
        return _request("POST", url, token, body_no_rel), False


def push_table_rows(token, dataset_id, table_name, rows):
    """POST `rows` to one table of the new dataset. Returns True on success."""
    url = f"{API_ROOT}/datasets/{dataset_id}/tables/{table_name}/rows"
    _request("POST", url, token, {"rows": rows})
    return True


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Create a NEW Power BI push-streaming dataset (meteolens-v2) to "
            "test whether REST-created datasets expose a key-based Push URL. "
            "Does not touch the existing production dataset."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the dataset schema JSON that would be posted and exit; no token, no network",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.dry_run:
        print(json.dumps(build_dataset_schema(include_relationships=True), indent=2))
        return 0

    try:
        token = get_access_token()

        existing = find_existing_dataset(token, DATASET_NAME)
        if existing is not None:
            print(
                f"error: a dataset named {DATASET_NAME!r} already exists "
                f"(id: {existing.get('id')}). Delete it in app.powerbi.com "
                "first (My workspace -> meteolens-v2 -> Settings -> Remove "
                "this dataset), then rerun this script.",
                file=sys.stderr,
            )
            return 1

        dataset, relationship_created = create_dataset(token)
        dataset_id = dataset["id"]
        dataset_name = dataset.get("name", DATASET_NAME)

        units_ok = True
        cities_ok = True
        units_error = None
        cities_error = None
        try:
            push_table_rows(token, dataset_id, "Units", UNIT_ROWS)
        except CreateDatasetError as exc:
            units_ok = False
            units_error = str(exc)

        try:
            push_table_rows(token, dataset_id, "Cities", CITY_ROWS)
        except CreateDatasetError as exc:
            cities_ok = False
            cities_error = str(exc)

    except CreateDatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("dataset created:")
    print(f"  id:   {dataset_id}")
    print(f"  name: {dataset_name}")
    print(f"  relationship created: {relationship_created}")
    print(
        f"  Units rows pushed: {'ok' if units_ok else 'FAILED - ' + units_error} "
        f"({len(UNIT_ROWS)} rows)"
    )
    print(
        f"  Cities rows pushed: {'ok' if cities_ok else 'FAILED - ' + cities_error} "
        f"({len(CITY_ROWS)} rows)"
    )
    print()
    print("next step (manual, in the browser):")
    print(
        "  1. Go to app.powerbi.com -> My workspace"
        "\n  2. Hover the 'meteolens-v2' dataset -> click 'More options' (...)"
        "\n  3. Click 'Settings', and look for a 'Push URL' / 'API Info' section"
        "\n  4. Report back whether a key-based Push URL is shown there"
    )

    if not units_ok or not cities_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

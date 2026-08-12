"""WMO weather interpretation codes used by Open-Meteo.

Single source of truth for the `weather_codes` table: the seed migration's insert
block is generated from `sql_seed()`. Run `python3 -m app.weather_codes` to print it.
"""

WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def describe(code: int | None) -> str:
    """Human-readable description for a WMO code.

    Unknown codes get a stable `Unknown (N)` label so ingest can insert the row and
    keep the weather_readings -> weather_codes foreign key intact.
    """
    if code is None:
        return "Unknown"
    return WMO_CODES.get(code, f"Unknown ({code})")


def sql_seed() -> str:
    """Idempotent insert block for the weather_codes table."""
    values = ",\n".join(
        f"  ({code}, '{description}')" for code, description in sorted(WMO_CODES.items())
    )
    return (
        "insert into public.weather_codes (code, description) values\n"
        f"{values}\n"
        "on conflict (code) do update set description = excluded.description;"
    )


if __name__ == "__main__":
    print(sql_seed())

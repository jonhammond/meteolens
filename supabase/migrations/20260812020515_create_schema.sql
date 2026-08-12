-- MeteoLens core schema: configurable locations, WMO code lookup, hourly readings.

create table public.locations (
  id bigint generated always as identity primary key,
  name text not null unique,
  latitude numeric(8,5) not null,
  longitude numeric(8,5) not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table public.weather_codes (
  code smallint primary key,
  description text not null              -- 0 'Clear sky', 61 'Slight rain', ...
);

create table public.weather_readings (
  id bigint generated always as identity primary key,
  location_id bigint not null references public.locations(id),
  recorded_at timestamptz not null,      -- Open-Meteo current.time normalized to UTC
  temperature_2m numeric(5,2),
  apparent_temperature numeric(5,2),
  relative_humidity_2m smallint,
  precipitation numeric(6,2),
  cloud_cover smallint,
  weather_code smallint references public.weather_codes(code),
  wind_speed_10m numeric(6,2),
  wind_gusts_10m numeric(6,2),
  inserted_at timestamptz not null default now(),
  unique (location_id, recorded_at)      -- upsert target; idempotent ingest
);

create index weather_readings_loc_time_idx
  on public.weather_readings (location_id, recorded_at desc);

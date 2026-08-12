alter table public.weather_readings
  add column dew_point_2m numeric(5,2),
  add column us_aqi smallint,                 -- EPA US AQI, unitless
  add column pm10 numeric(6,2),               -- µg/m³
  add column pm2_5 numeric(6,2),              -- µg/m³
  add column temperature_2m_f numeric(6,2)
    generated always as (temperature_2m * 9.0 / 5.0 + 32.0) stored,
  add column apparent_temperature_f numeric(6,2)
    generated always as (apparent_temperature * 9.0 / 5.0 + 32.0) stored,
  add column dew_point_2m_f numeric(6,2)
    generated always as (dew_point_2m * 9.0 / 5.0 + 32.0) stored,
  add column wind_speed_10m_mph numeric(6,2)
    generated always as (wind_speed_10m / 1.609344) stored,
  add column wind_gusts_10m_mph numeric(6,2)
    generated always as (wind_gusts_10m / 1.609344) stored,
  add column precipitation_in numeric(6,3)
    generated always as (precipitation / 25.4) stored;

alter table public.weather_readings
  add column us_aqi_pm2_5 smallint,            -- EPA US AQI sub-index for PM2.5, unitless
  add column us_aqi_pm10 smallint;              -- EPA US AQI sub-index for PM10, unitless

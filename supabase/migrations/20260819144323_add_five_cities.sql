-- Five additional Colorado towns. Coordinates verified against Open-Meteo's
-- geocoding API; all America/Denver. Idempotent, matching the original seed.
insert into public.locations (name, latitude, longitude) values
  ('Trinidad', 37.16946, -104.50054),
  ('Sterling', 40.62554, -103.20771),
  ('Dinosaur', 40.24358, -109.01456),
  ('Cortez',   37.34888, -108.58593),
  ('Walsh',    37.38613, -102.27824)
on conflict (name) do update set
  latitude = excluded.latitude,
  longitude = excluded.longitude;

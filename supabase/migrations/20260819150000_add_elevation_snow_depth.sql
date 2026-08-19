alter table public.locations
  add column elevation numeric(6,1);         -- meters; Open-Meteo elevation API (90m Copernicus DEM)

alter table public.weather_readings
  add column snow_depth numeric(6,3),         -- meters (Open-Meteo native unit)
  add column snow_depth_in numeric(7,3)
    generated always as (snow_depth / 0.0254) stored;

-- Idempotent elevation backfill for the existing seeded locations. Values
-- fetched from Open-Meteo's elevation API for the coordinates stored in
-- seed_reference_data.sql / add_five_cities.sql.
update public.locations as l set
  elevation = v.elevation
from (values
  ('Denver',            1615.0),
  ('Colorado Springs',  1832.0),
  ('Pueblo',            1421.0),
  ('Leadville',         3098.0),
  ('Fort Collins',      1528.0),
  ('Durango',           1992.0),
  ('Grand Junction',    1398.0),
  ('Glenwood Springs',  1759.0),
  ('Steamboat Springs', 2051.0),
  ('Castle Rock',       1896.0),
  ('Longmont',          1520.0),
  ('Boulder',           1624.0),
  ('Trinidad',          1835.0),
  ('Sterling',          1202.0),
  ('Dinosaur',          1803.0),
  ('Cortez',            1890.0),
  ('Walsh',             1206.0)
) as v(name, elevation)
where l.name = v.name;

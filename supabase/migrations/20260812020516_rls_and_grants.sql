-- Deny-by-default access model.
--
-- The cloud project has "automatically expose new tables" OFF, so anon/authenticated
-- never receive table privileges there. The LOCAL stack still auto-grants them via
-- default privileges, so the revokes below are what make local match prod; they are a
-- harmless no-op against the cloud. RLS with no policies is the second lock.

alter table public.locations        enable row level security;
alter table public.weather_codes    enable row level security;
alter table public.weather_readings enable row level security;

revoke all on public.locations, public.weather_codes, public.weather_readings
  from anon, authenticated;

-- Flask's secret key maps to service_role (BYPASSRLS); least privilege only, no deletes.
grant usage on schema public to service_role;
grant select on public.locations to service_role;
grant select, insert on public.weather_codes to service_role;             -- Unknown (N) fallback
grant select, insert, update on public.weather_readings to service_role;  -- upsert
-- Identity columns need no sequence grants (unlike serial).

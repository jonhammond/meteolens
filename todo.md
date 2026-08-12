# MeteoLens — Active Task Checklist

Granular task list derived from [PLAN.md](PLAN.md). Each phase maps 1:1 to a PLAN.md milestone; the **Accept** line under each phase is that milestone's exit criterion. Items tagged **[USER]** require actions in an external dashboard/account that only Jon can perform (Supabase, Render, DNS, Power BI, cron-job.org).

## Phase M1 — Supabase schema (local-first, then pushed)

- [x] **[USER]** Create free Supabase project with **Enable Data API ON**, **automatic RLS ON**, **automatically expose new tables OFF**; note project URL and a secret (`sb_secret_...`) key
- [x] `supabase init` — create `supabase/config.toml`
- [x] `supabase start` — bring up the local Docker stack (ports shifted +10 to 54331+ to coexist with another local project on the defaults)
- [x] Write `app/weather_codes.py` — full WMO code → description dict (28 codes, single source of truth), `describe()` with `Unknown (N)` fallback, `sql_seed()` generator runnable as `python3 -m app.weather_codes`
- [x] `supabase migration new create_schema` — `locations`, `weather_codes`, `weather_readings` tables, `unique (location_id, recorded_at)`, `weather_readings_loc_time_idx`
- [x] `supabase migration new rls_and_grants` — enable RLS on all three tables, no anon policies (deny-by-default), explicit `revoke all ... from anon, authenticated` (makes local match the cloud's auto-expose-OFF), least-privilege `service_role` grants (no deletes anywhere)
- [x] `supabase migration new seed_reference_data` — idempotent inserts: WMO codes (generated from `weather_codes.py`) + the 12 Colorado cities with verified coordinates
- [x] `supabase db reset` — replayed cleanly twice; counts stable at 28 codes / 12 locations (seeds are idempotent)
- [x] Local accept tests: 3 tables with RLS enabled, 28 codes, 12 locations, grants correct (anon/authenticated absent entirely); anon-key REST select → `42501` permission denied; secret-key REST select → rows
- [x] **[USER]** `supabase login` — not needed; an existing CLI session was already in the keychain. Project ref `yddbzlmdaqrpzuumeodg` (us-east-2)
- [x] `supabase link --project-ref yddbzlmdaqrpzuumeodg` then `supabase db push` — all three migrations applied to the cloud
- [x] Cloud accept tests: `supabase migration list` shows all three local/remote versions matching; remote query returns 28 codes / 12 locations / 3 RLS-enabled tables; publishable-key REST select → HTTP 401 `42501` permission denied (verified by Jon)

**Accept (local):** `db reset` replays cleanly twice; anon-key select → permission denied; service-key select → rows; seeds present.
**Accept (cloud):** all three migrations applied remotely; tables in dashboard; publishable-key select → permission denied; seeds present.

## Phase M2 — Flask skeleton

- [ ] Scaffold repo layout per PLAN.md (`app/`, `sql/`, `scripts/`, `wsgi.py`, `requirements.txt`, `render.yaml`, `.env.example`)
- [ ] `app/config.py` — required env vars `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `POWERBI_PUSH_URL`, `INGEST_TOKEN`; optional `POWERBI_EMBED_URL`; no hardcoded fallbacks (project security rule)
- [ ] `app/__init__.py` — `create_app()` with fail-fast env validation at startup
- [ ] `app/routes.py` — `/healthz` → 200
- [ ] `wsgi.py` exposes `app`; `requirements.txt` — flask, gunicorn, supabase, requests
- [ ] `.env.example` — variable names only; confirm `.env` is gitignored

**Accept:** `gunicorn wsgi:app` runs locally with env populated; refuses to start with one var unset; `curl /healthz` → 200.

## Phase M3 — Ingestion endpoint (dual write)

- [ ] `app/open_meteo.py` — HTTP client for the `current` block (fields per API_PLAN.md), `timezone=auto`, `timeformat=unixtime`, descriptive User-Agent, one retry on 5xx
- [ ] `app/db.py` — supabase-py client wrapper (REST over HTTPS; Render is IPv4-only)
- [ ] `app/powerbi.py` — batched POST to push URL (≤1 req/sec, ≤10k rows; all locations in one POST)
- [ ] `app/ingest.py` — per-location orchestration: fetch → Supabase upsert `on conflict (location_id, recorded_at)` → PBI push; per-location try/except so one failure never aborts the run; unseen WMO code inserted as `Unknown (N)` before the reading
- [ ] PBI row enrichment computed server-side in Python: `location`, `weather_desc`, `precip_color`/`temp_color`/`cloud_color` hex strings, `precip_flag`/`temp_band`/`cloud_band` numeric helpers
- [ ] `POST /api/ingest` — `Authorization: Bearer <INGEST_TOKEN>` with constant-time compare; 401 on bad token; 405 on GET; returns per-location `{location: ok|error}` summary
- [ ] `GET /api/latest` — newest reading per active location (for frontend cards)

**Accept:** authed curl → summary JSON; rows in Supabase; re-run adds no dupes; wrong token → 401; GET → 405.

## Phase M4 — Frontend page

- [ ] `app/templates/index.html` — title, server-rendered per-location latest-conditions cards, responsive Power BI `<iframe>` from `POWERBI_EMBED_URL` with "report pending" placeholder until M7
- [ ] `app/static/style.css` — plain CSS3, no JS build; note "updates hourly" on the page

**Accept:** renders locally with real card data + placeholder iframe.

## Phase M5 — Render deploy + DNS

- [ ] `render.yaml` — python web service, `startCommand: gunicorn wsgi:app`, `healthCheckPath: /healthz`, all secrets `sync: false`
- [ ] **[USER]** Create Render service from repo; enter the four secrets in the Render dashboard
- [ ] **[USER]** Add custom domain `meteolens.jonhammond.org`; add the CNAME Render specifies at the jonhammond.org DNS host; wait for auto-TLS

**Accept:** `https://meteolens.jonhammond.org/healthz` → 200 with valid cert; authed ingest curl against prod writes rows.

## Phase M6 — cron-job.org wiring

- [ ] **[USER]** Job 1: pre-warm GET `/healthz` at `57 * * * *`
- [ ] **[USER]** Job 2: ingest POST `/api/ingest` at `0 * * * *` with Bearer header stored in job config
- [ ] **[USER]** Enable failure notifications on both jobs

**Accept:** both green in execution history over 2+ hours; one new row/location/hour in Supabase.

## Phase M7 — Power BI dataset, report, embed

- [ ] **[USER]** New → Streaming dataset → API, name `meteolens`, fields per PLAN.md M7 step 1, **Historic data analysis: ON**; copy Push URL → set `POWERBI_PUSH_URL` on Render (secret — never in git)
- [ ] **[USER]** Verify the Publish-to-web menu item exists early (tenant setting risk from PLAN.md)
- [ ] Write `scripts/backfill_powerbi.py` — replay Supabase history into a (re)created push dataset
- [ ] Trigger one ingest so rows exist; run backfill if Supabase already holds history
- [ ] **[USER]** Create report: temp line chart, wind combo chart, temp×humidity scatter, 3 conditional KPI cards (Field value → `*_color` columns), slicers on `weather_desc` + `location`
- [ ] **[USER]** File → Embed report → Publish to web → copy embed URL → set `POWERBI_EMBED_URL` on Render → redeploy

**Accept:** report renders logged-out at the public URL and inside meteolens.jonhammond.org; slicers filter all visuals.

## Phase M8 — End-to-end verification

- [ ] Local: POST `/api/ingest` with token → 200 + per-location `ok`; wrong token → 401; GET → 405
- [ ] Supabase: one new UTC row per active location; re-run adds no dupes; publishable/anon key gets permission denied
- [ ] Power BI: dataset row count grows after ingest; report shows the new hour
- [ ] `https://meteolens.jonhammond.org/` resolves with valid TLS; cards match Supabase latest rows
- [ ] cron-job.org: pre-warm at :57 and ingest at :00 succeed 3 consecutive hours
- [ ] Public embed loads in incognito; data ≤ ~1 h stale
- [ ] Kill test: unset one env var locally → app refuses to start

---

## Session Log & Active Tasks

**Last updated:** 2026-08-11

- **Current focus:** Phase M1 — local half is **complete and verified**. Workflow changed 2026-08-11 to local-first (Supabase CLI + Docker); PLAN.md amended accordingly (`sql/001–003` replaced by `supabase/migrations/`). Three migrations written and passing all local accept tests. Remaining M1 work is promoting them to the cloud project.
- **Notes from this session:**
  - Local ports shifted +10 (54331+) in `supabase/config.toml`; another local project (`its_the_loop`) holds the default 54321+ range, and both now run side by side.
  - Local stack auto-grants to `anon`/`authenticated`, unlike the cloud with auto-expose OFF — the explicit `revoke` in `rls_and_grants` is what makes the two environments match.
  - Two migrations initially shared a timestamp (`db reset` failed on the migration table's primary key); the seed migration was renamed to `20260812020517`. Create migrations one at a time, or verify timestamps differ.
- **Blockers:** Cloud push needs `supabase login` — interactive browser auth that only Jon can complete.
- **Next immediate step:** Jon runs `! supabase login` and shares the project ref; then `supabase link --project-ref <ref>` + `supabase db push` (confirm first — it mutates the cloud DB), followed by the cloud accept tests.

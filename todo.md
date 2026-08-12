# MeteoLens — Active Task Checklist

Granular task list derived from [PLAN.md](PLAN.md). Each phase maps 1:1 to a PLAN.md milestone; the **Accept** line under each phase is that milestone's exit criterion. Items tagged **[USER]** require actions in an external dashboard/account that only Jon can perform (Supabase, Render, DNS, Power BI, cron-job.org).

## Phase M1 — Supabase schema

- [ ] **[USER]** Create free Supabase project; note project URL and service-role key
- [ ] Write `app/weather_codes.py` — full WMO code → description dict (~28 codes, single source of truth) with a helper that emits the SQL seed
- [ ] Write `sql/001_schema.sql` — `locations`, `weather_codes`, `weather_readings` tables, `unique (location_id, recorded_at)`, `weather_readings_loc_time_idx`
- [ ] Write `sql/002_rls.sql` — enable RLS on all three tables, no anon policies (deny-by-default)
- [ ] Write `sql/003_seed.sql` — WMO codes (generated from `weather_codes.py`) + the 12 Colorado cities with verified coordinates
- [ ] **[USER]** Run `sql/001–003` in the Supabase SQL Editor
- [ ] Verify accept criteria: tables visible in dashboard; anon-key select returns zero rows; seeds present

**Accept:** tables in dashboard; anon-key select returns zero rows; seeds present.

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
- [ ] Supabase: one new UTC row per active location; re-run adds no dupes; anon key sees zero rows
- [ ] Power BI: dataset row count grows after ingest; report shows the new hour
- [ ] `https://meteolens.jonhammond.org/` resolves with valid TLS; cards match Supabase latest rows
- [ ] cron-job.org: pre-warm at :57 and ingest at :00 succeed 3 consecutive hours
- [ ] Public embed loads in incognito; data ≤ ~1 h stale
- [ ] Kill test: unset one env var locally → app refuses to start

---

## Session Log & Active Tasks

**Last updated:** 2026-08-11

- **Current focus:** Project bootstrap. `todo.md` generated from PLAN.md milestones M1–M8; no application code written yet.
- **Blockers:** None. (M1 needs the Supabase project created before SQL can be run, but the SQL files themselves can be written first.)
- **Next immediate step:** Phase M1 — write `app/weather_codes.py` (WMO dict) and generate `sql/001_schema.sql`, `sql/002_rls.sql`, `sql/003_seed.sql` from it.

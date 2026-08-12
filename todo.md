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

- [x] Scaffold repo layout per PLAN.md (`app/`, `wsgi.py`, `requirements.txt`, `.env.example`) — `scripts/` deferred to M7 (backfill), `render.yaml` to M5; `sql/` obsolete (replaced by `supabase/migrations/` in M1)
- [x] `app/config.py` — required env vars `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `POWERBI_PUSH_URL`, `INGEST_TOKEN`; optional `POWERBI_EMBED_URL`; no hardcoded fallbacks (project security rule); blank/whitespace values treated as missing
- [x] `app/__init__.py` — `create_app()` with fail-fast env validation at startup (raises `ConfigError`); config stored at `app.config["METEOLENS"]`
- [x] `app/routes.py` — `/healthz` → 200 `{"status":"ok"}` (blueprint `main`)
- [x] `wsgi.py` exposes `app`; `requirements.txt` — flask 3.1.2, gunicorn 23.0.0, supabase 2.31.0, requests 2.32.5
- [x] `.env.example` — variable names only; `.gitignore` extended with `.env`, `__pycache__/`, `.venv/`; `git check-ignore` confirms `.env` ignored

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

- **Current focus:** **Phase M2 is COMPLETE** — Flask skeleton scaffolded, both accept criteria verified (gunicorn serves `/healthz` → 200 with env populated; blanking `INGEST_TOKEN` makes it exit 3 with `ConfigError: Missing required environment variable(s): INGEST_TOKEN`). Ready to start M3 (ingestion endpoint).
- **Environment facts to reuse:**
  - Python 3.11.14 (pyenv). Virtualenv at `.venv/` (gitignored); run things as `.venv/bin/python` / `.venv/bin/gunicorn`.
  - Local dev `.env` exists (gitignored, `chmod 600`) with `SUPABASE_URL=http://127.0.0.1:54331`, the local `SECRET_KEY` from `supabase status`, a generated `INGEST_TOKEN`, and a placeholder `POWERBI_PUSH_URL` (`https://placeholder.invalid/pending-m7`) — swap in the real push URL during M7.
  - PLAN.md M2 text says local Supabase is on `54321`; the actual local port is **54331** (the documented +10 shift). Trust 54331.
  - `supabase==2.22.0` is **yanked** on PyPI (unpinned transitive deps); pinned `2.31.0` instead.
  - Cloud project ref `yddbzlmdaqrpzuumeodg` (us-east-2), linked. Promote schema changes with a new migration + `supabase db push` — never hand-edit in the cloud SQL Editor.
  - Local stack runs on **54331+** (ports shifted +10 in `supabase/config.toml`) because another local project (`its_the_loop`) holds the default 54321+ range. Local API `http://127.0.0.1:54331`, Studio `http://127.0.0.1:54333`; keys come from `supabase status`.
  - Local auto-grants to `anon`/`authenticated`, unlike the cloud with auto-expose OFF — the explicit `revoke` in `rls_and_grants` is what keeps the two environments equivalent. Keep it in any future table's migration.
  - Migration timestamps are the migration table's primary key: two files created in the same second collided and broke `db reset`. Create them one at a time and check timestamps differ.
  - `supabase db push` may print a non-fatal `pg-delta` catalog-caching error *after* migrations apply; verify with `supabase migration list`, not the exit message.
- **Blockers:** None.
- **Next immediate step:** Phase M3 — ingestion endpoint: `app/open_meteo.py` (current block, `timezone=auto`, `timeformat=unixtime`), `app/db.py` (supabase-py REST wrapper), `app/powerbi.py` (batched push), `app/ingest.py` (per-location fetch → upsert → push with per-location try/except), then `POST /api/ingest` (Bearer + constant-time compare) and `GET /api/latest`.

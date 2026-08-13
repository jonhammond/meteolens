# Service-Principal Embedding Plan ("Embed for your customers")

Plan to replace the never-used Publish-to-web embed with a service-principal
embed so https://meteolens.jonhammond.org/ renders the Power BI report for
anonymous visitors. Drafted 2026-08-12; awaiting Jon's approval before any
implementation.

---

## 1. Why

The site shows the "Full report coming soon" placeholder because
`POWERBI_EMBED_URL` was designed for a **Publish to web** URL
(`https://app.powerbi.com/view?r=...`), and Publish to web is **permanently
unavailable for this report** — it uses report-level (extension) DAX measures
and a live connection to a push dataset, both on Microsoft's
[unsupported list](https://learn.microsoft.com/power-bi/collaborate-share/service-publish-to-web#considerations-and-limitations)
(items 3 and 11). No tenant setting changes that.

The supported alternative is
[**Embed for your customers**](https://learn.microsoft.com/power-bi/developer/embedded/embed-service-principal):
the Flask app authenticates to Entra as a **service principal**, mints
short-lived **view-only embed tokens** server-side via
[Generate Token](https://learn.microsoft.com/power-bi/developer/embedded/generate-embed-token),
and the page renders the report with the `powerbi-client` JS SDK. Visitors
never sign in; every report feature (extension measures, Metric/Imperial
toggle, map) keeps working because the report renders exactly as it does in
the service.

## 2. Architecture

```
browser ── GET / ──────────────► Flask (Render)
browser ── GET /api/embed-token ► Flask:
                                    1. AAD token (client credentials,
                                       cached in-process ~55 min)
                                    2. POST /v1.0/myorg/GenerateToken
                                       (cached until ~5 min before expiry)
                                 ◄─ {token, embedUrl, reportId, expiresAt}
browser ── powerbi.min.js (vendored, no CDN) renders the report
        ── re-fetches token before expiry → report.setAccessToken()
```

Design points:

- **One shared token for all visitors.** The report is public and has no RLS
  or per-user identity, so the server caches a single embed token and hands
  the same one to every visitor until it nears expiry (~1 GenerateToken call
  per hour — negligible capacity load).
- **`/api/embed-token` is public by design.** It exposes only a view-only
  token scoped to this one report (no `allowEdit`, no `allowSaveAs`, no
  workspace access) — the same access a Publish-to-web link would have given.
- **No new Python dependency.** The client-credentials flow is one
  `requests.post` to
  `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token` with
  `grant_type=client_credentials` and
  `scope=https://analysis.windows.net/powerbi/api/.default`, following the
  existing `app/powerbi.py` HTTP style. No msal.

Fixed identifiers (already known, not secrets):

| Thing | Value |
|---|---|
| Workspace (JHammoFabric) | `7a102e5b-56e2-4dbc-b04c-5cedff7c3b0e` |
| Report (meteolens) | `7e2b2826-7e58-4165-bb1e-75b35bed31dc` |
| Dataset (meteolens-v2) | `2d64d5e3-8acf-44f8-b626-bd85a8757c26` |

## 3. [USER] Part 1 — Create the Entra app registration

Only Jon can do this (Azure portal + secret handling).

1. Open https://portal.azure.com → search **App registrations** → open it.
2. Click **+ New registration**.
   - **Name:** `meteolens-embed`
   - **Supported account types:** *Accounts in this organizational directory
     only* (single tenant).
   - **Redirect URI:** leave empty.
   - Click **Register**.
3. On the app's **Overview** page, copy two values (needed in Part 4):
   - **Application (client) ID** → becomes `POWERBI_CLIENT_ID`
   - **Directory (tenant) ID** → becomes `POWERBI_TENANT_ID`
4. Left sidebar → **Certificates & secrets** → **+ New client secret**.
   - **Description:** `meteolens-render`; **Expires:** 24 months → **Add**.
   - Copy the **Value** column immediately (it is shown only once). This is
     `POWERBI_CLIENT_SECRET` — a secret: paste it only into Render and your
     local `.env`, never into chat, git, or TODO.md.
5. Do **not** add anything under **API permissions**. Microsoft's docs
   explicitly say service-principal embedding needs no API permissions and
   that adding them causes hard-to-troubleshoot errors.

## 4. [USER] Part 2 — Security group + Fabric tenant settings

1. Azure portal → **Microsoft Entra ID** → **Groups** → **New group**.
   - **Group type:** Security; **Name:** `PowerBI-Embed-SPs`.
   - **Members** → add `meteolens-embed` → **Create**.
2. https://app.powerbi.com → gear icon → **Admin portal** → **Tenant
   settings** → scroll to **Developer settings**:
   - **Embed content in apps** → Enabled → *Specific security groups* →
     `PowerBI-Embed-SPs` → **Apply**.
   - **Service principals can call Fabric public APIs** (older tenants name
     it "Allow service principals to use Power BI APIs") → Enabled →
     *Specific security groups* → `PowerBI-Embed-SPs` → **Apply**.
3. Tenant settings take ~15 minutes to propagate.

## 5. [USER] Part 3 — Give the service principal workspace access

1. https://app.powerbi.com → **Workspaces** → **JHammoFabric** → **Manage
   access** (top right).
2. **+ Add people or groups** → type `meteolens-embed` → select it → role
   **Member** → **Add**.

## 6. Code changes (assistant, after this document is approved)

| File | Change |
|---|---|
| `app/config.py` | Add `POWERBI_TENANT_ID`, `POWERBI_CLIENT_ID`, `POWERBI_CLIENT_SECRET`, `POWERBI_WORKSPACE_ID`, `POWERBI_REPORT_ID` to `OPTIONAL_VARS`; add an `embed_configured` property (all five present). Remove `POWERBI_EMBED_URL` and `_is_publish_to_web_url` — dead code once Publish-to-web is abandoned. No hardcoded fallbacks, ever. |
| `app/embed.py` (new) | Mirrors `app/powerbi.py` style (`requests`, 10 s timeout, typed `EmbedError`, secrets never logged). `get_aad_token(cfg)` client-credentials POST; `get_embed_token(cfg)` fetches the report's `embedUrl` (GET report-in-group) and POSTs `GenerateToken` (V2 body: `{reports:[{id}], datasets:[{id}], targetWorkspaces:[{id}]}`); module-level cache returns the same token until ~5 min before `expiration`. |
| `app/routes.py` | New `GET /api/embed-token`: 200 `{token, embedUrl, reportId, expiresAt}`; 503 `{error: "..."}` when unconfigured or the upstream calls fail (page falls back to the placeholder). |
| `app/templates/index.html` | Replace the iframe branch with `<div id="report-container">` plus the existing placeholder; small inline script: fetch `/api/embed-token` → `powerbi.embed(...)` with `tokenType: models.TokenType.Embed`, filter pane hidden, page navigation off → schedule a re-fetch before `expiresAt` and call `report.setAccessToken()`. Any failure leaves the placeholder visible. |
| `app/static/powerbi.min.js` (new) | Vendored pinned `powerbi-client` dist (version recorded in a header comment). Self-hosted — no CDN dependency. |
| `app/static/style.css` | Responsive `#report-container` sizing (16:9-ish, `max-width: 100%`). |
| `render.yaml` | The five new env vars, all `sync: false`. |
| `.env.example` | The five names with comments (names only, no values). |
| `tests/` (new) | Minimal `pytest`: `tests/test_embed.py` (mocked `requests`: caching, expiry refresh, failure → `EmbedError`) and `tests/test_routes.py` (503 unconfigured, 200 with mocked embed module). Dev-only dependency via `requirements-dev.txt` (`pytest`); production `requirements.txt` unchanged. |

Nothing is committed or pushed; all changes stay in the working tree per
project git rules.

## 7. [USER] Part 4 — Configure Render (and local .env)

1. https://dashboard.render.com → **meteolens** service → **Environment**.
2. Add five variables (**Add Environment Variable**), then **Save Changes**
   (Render auto-redeploys):

   | Key | Value |
   |---|---|
   | `POWERBI_TENANT_ID` | Directory (tenant) ID from Part 1 |
   | `POWERBI_CLIENT_ID` | Application (client) ID from Part 1 |
   | `POWERBI_CLIENT_SECRET` | the secret Value from Part 1 |
   | `POWERBI_WORKSPACE_ID` | `7a102e5b-56e2-4dbc-b04c-5cedff7c3b0e` |
   | `POWERBI_REPORT_ID` | `7e2b2826-7e58-4165-bb1e-75b35bed31dc` |

3. Add the same five lines to the local `.env` so the app can be smoke-tested
   locally before relying on the deploy.

## 8. Verification (in order)

1. **[USER] Local smoke test** (assistant is blocked from `.env`-sourcing
   commands; run these with the `!` prefix):

   ```
   ! set -a; source .env; set +a; .venv/bin/python -m flask --app wsgi run --port 5001 &
   ! curl -s http://localhost:5001/api/embed-token | jq 'keys'
   ```

   Expect `["embedUrl","expiresAt","reportId","token"]`. (Then stop the dev
   server.)
2. **Tests:** `.venv/bin/python -m pytest` passes (no secrets required —
   everything upstream is mocked).
3. **After the Render deploy** (assistant, no secrets needed):
   `curl https://meteolens.jonhammond.org/api/embed-token` → 200 with the four
   keys; `curl https://meteolens.jonhammond.org/` → HTML contains
   `report-container` and no `report-pending`.
4. **[USER] Browser check:** open https://meteolens.jonhammond.org/ — the
   report should render inline; the unit toggle, city slicer, and map should
   behave exactly as they do at app.powerbi.com.

## 9. Security & cost notes

- **Secret lifecycle:** the client secret expires in 24 months; set a
  reminder to rotate it in Entra → Certificates & secrets and update Render.
- **Token scope:** view-only, single report, single dataset; no edit/save-as;
  the service principal itself holds workspace Member rights, but the token
  handed to browsers cannot reach anything beyond this report.
- **Public endpoint:** `/api/embed-token` gives anonymous visitors the same
  view access a Publish-to-web link would have. If that ever becomes
  unacceptable, the endpoint (not the report) is the thing to gate.
- **Capacity cliff (important):** JHammoFabric runs on a **Fabric trial
  capacity (~59 days remaining as of 2026-08-12)**. Embed-for-customers
  requires a capacity in production — when the trial lapses, embedding stops
  working until the workspace is assigned a paid capacity (e.g. **F2
  pay-as-you-go**, which can be paused when idle) or another arrangement is
  chosen. Decide before the trial ends.

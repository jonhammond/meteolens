# Unit toggle for the city cards

## Context

The MeteoLens home page (`app/templates/index.html`) shows the embedded Power BI report with a grid of 12 city cards below it. The cards are server-rendered in metric only (°C, mm, km/h), while the report already has its own Metric/Imperial slicer. The user wants a toggle so visitors can switch the units shown on the cards.

**Decisions confirmed with the user:** toggle controls the cards only (no sync with the report's slicer); the choice persists across visits via localStorage, defaulting to Metric.

**Key fact that shapes the design:** no backend work is needed. `db.fetch_latest_per_location()` (`app/db.py:119-185`) already returns both unit systems for every reading — `temperature_2m`/`temperature_2m_f`, `apparent_temperature`/`apparent_temperature_f`, `wind_speed_10m`/`wind_speed_10m_mph`, `wind_gusts_10m`/`wind_gusts_10m_mph`, `precipitation`/`precipitation_in` — and the `index()` route (`app/routes.py:38-53`) passes those rows straight to the template. Humidity, cloud cover, and AQI are unitless and unaffected.

## Approach

Render **both** unit variants of each affected value into the HTML at server-render time, and toggle which one is visible with a single class on a wrapper element. No fetch, no re-render, no framework — consistent with the page's existing vanilla HTML/CSS/minimal-JS style. Metric stays the no-JS default (progressive enhancement: with JS disabled the toggle is hidden and cards show metric, exactly as today).

## Changes

### 1. `app/templates/index.html`

**Toggle control** — insert at the top of the cards section (line ~32, inside `<section aria-label="Latest conditions by location">`, before the card grid, so it visually belongs to the cards and not the report):

- A small segmented control, hidden by default and revealed by JS (so no dead control without JS):
  ```html
  <div class="unit-toggle" id="unit-toggle" role="group" aria-label="Units" hidden>
    <button type="button" data-unit="metric" aria-pressed="true">Metric</button>
    <button type="button" data-unit="imperial" aria-pressed="false">Imperial</button>
  </div>
  ```

**Dual-value markup** — for each of the five unit-bearing values, replace the single Jinja expression with a metric span + imperial span, keeping the existing `is not none` guards and format precision per value. Pattern (temperature example, replacing lines 40-42):

```html
<p class="card-temp">
  <span class="val-metric">{{ "%.0f"|format(r.temperature_2m) ~ "°C" if r.temperature_2m is not none else "—" }}</span>
  <span class="val-imperial">{{ "%.0f"|format(r.temperature_2m_f) ~ "°F" if r.temperature_2m_f is not none else "—" }}</span>
</p>
```

Apply the same pattern to:
- `card-temp` (°C / °F) — `temperature_2m` / `temperature_2m_f`
- `card-feelslike` (°C / °F) — `apparent_temperature` / `apparent_temperature_f` (keep the leading "Feels like" text outside the spans)
- Precipitation `<dd>` (`%.1f` mm / `%.2f` in — inches need two decimals or most hourly values round to 0.0) — `precipitation` / `precipitation_in`
- Wind `<dd>` (`%.0f` km/h / mph) — `wind_speed_10m` / `wind_speed_10m_mph`
- Gusts `<dd>` (`%.0f` km/h / mph) — `wind_gusts_10m` / `wind_gusts_10m_mph`

**JS** — a small inline script at the end of `<body>` (outside the `{% if embed_configured %}` block, since it must work regardless of embed state), matching the existing IIFE style at lines 99-175:

- On load: read `localStorage.getItem("meteolens-units")`; if `"imperial"`, add class `units-imperial` to the card grid's section (or `<body>`); set the matching button's `aria-pressed`; un-`hidden` the toggle.
- On button click: set/remove the `units-imperial` class, update both buttons' `aria-pressed`, and `localStorage.setItem("meteolens-units", unit)`.
- Wrap localStorage access in try/catch (Safari private mode throws) — toggle still works for the session if storage is unavailable.

### 2. `app/static/style.css`

- Visibility rules — the core mechanism:
  ```css
  .val-imperial { display: none; }
  .units-imperial .val-metric { display: none; }
  .units-imperial .val-imperial { display: inline; }
  ```
- `.unit-toggle` styling: reuse the existing tokens (`--surface`, `--border`, `--accent`, `--radius`, `--shadow` in `:root`, `style.css:1-9`). Two-button segmented pill; active button gets `--accent` background with white text, inactive gets `--surface`/`--text-muted`. Right-aligned above the card grid with a small margin.

## Files touched

| File | Change |
|------|--------|
| `app/templates/index.html` | Toggle markup, dual-value spans (5 values × 12 cards via the loop), unit-toggle IIFE script |
| `app/static/style.css` | `.unit-toggle` styles + `.val-metric`/`.val-imperial` visibility rules |

No changes to `app/routes.py`, `app/db.py`, or the database.

## Verification

1. Run the app locally (Flask dev server) and load `/`.
2. Confirm cards initially show °C/mm/km/h and the toggle appears with **Metric** active.
3. Click **Imperial** → all five values on every card switch to °F/in/mph; humidity, cloud cover, AQI, and observed-time are unchanged; the report embed is untouched.
4. Reload the page → Imperial is still selected (localStorage persistence). Switch back to Metric, reload, confirm it sticks.
5. Check a location with a null reading (or temporarily null one in the data) → both unit variants render "—".
6. Disable JS (or block the script) → page shows metric values with no toggle visible, matching current behavior.

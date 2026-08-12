"""HTTP routes.

M3 adds the ingest endpoint (dual write to Supabase + Power BI) and the
read-only latest-conditions endpoint the M4 cards will consume. M4 adds the
server-rendered page that displays those cards plus the Power BI embed.
"""

import hmac
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request

from app import db, ingest

bp = Blueprint("main", __name__)


def _format_recorded_at(value):
    """Render an ISO-8601 UTC timestamp (e.g. "2026-08-11T14:00:00+00:00") for humans.

    Python 3.11's fromisoformat handles the "+00:00" offset PostgREST returns
    directly, so no extra parsing dependency is needed.
    """
    if not value:
        return "—"
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M UTC")


@bp.get("/healthz")
def healthz():
    """Liveness probe: Render health check and the cron :57 pre-warm target."""
    return jsonify(status="ok"), 200


@bp.get("/")
def index():
    """Latest-conditions page: one card per location plus the Power BI embed.

    POWERBI_EMBED_URL is None until M7, so the template falls back to a
    "report pending" placeholder instead of an iframe with an empty src.
    """
    cfg = current_app.config["METEOLENS"]
    client = db.build_client(cfg)
    readings = db.fetch_latest_per_location(client)
    for reading in readings:
        reading["recorded_at_display"] = _format_recorded_at(reading["recorded_at"])
    return render_template(
        "index.html", readings=readings, powerbi_embed_url=cfg.POWERBI_EMBED_URL
    )


@bp.post("/api/ingest")
def api_ingest():
    """Trigger one ingest run. Declaring methods=["POST"] makes GET 405 automatically.

    Auth is a shared-secret bearer token compared with hmac.compare_digest to
    avoid a timing side channel on the comparison.
    """
    cfg = current_app.config["METEOLENS"]

    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme != "Bearer" or not token or not hmac.compare_digest(token, cfg.INGEST_TOKEN):
        return jsonify(error="unauthorized"), 401

    summary = ingest.run_ingest(cfg)
    return jsonify(summary), 200


@bp.get("/api/latest")
def api_latest():
    """Newest reading per active location. No auth: read-only, non-sensitive."""
    cfg = current_app.config["METEOLENS"]
    client = db.build_client(cfg)
    return jsonify(db.fetch_latest_per_location(client)), 200

"""HTTP routes.

M3 adds the ingest endpoint (dual write to Supabase + Power BI) and the
read-only latest-conditions endpoint the M4 cards will consume.
"""

import hmac

from flask import Blueprint, current_app, jsonify, request

from app import db, ingest

bp = Blueprint("main", __name__)


@bp.get("/healthz")
def healthz():
    """Liveness probe: Render health check and the cron :57 pre-warm target."""
    return jsonify(status="ok"), 200


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

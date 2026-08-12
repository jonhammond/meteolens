"""HTTP routes.

M2 provides liveness only; the ingest and page routes arrive in M3/M4.
"""

from flask import Blueprint, jsonify

bp = Blueprint("main", __name__)


@bp.get("/healthz")
def healthz():
    """Liveness probe: Render health check and the cron :57 pre-warm target."""
    return jsonify(status="ok"), 200

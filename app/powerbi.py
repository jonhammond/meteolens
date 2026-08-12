"""Power BI streaming-dataset push client.

NOTE: `POWERBI_PUSH_URL` is a placeholder (`https://placeholder.invalid/pending-m7`)
in local dev, so pushes are EXPECTED to fail until the real streaming dataset
is created in M7. ingest.py treats this as a separate, non-fatal failure —
Supabase is the system of record, not Power BI.
"""

import requests

TIMEOUT_SECONDS = 10


class PowerBIError(RuntimeError):
    """Raised when the push request fails or returns a non-2xx status."""


def push_rows(push_url, rows):
    """Push all rows in a single batched POST.

    Power BI push datasets accept up to 10k rows/request and ask for at most
    ~1 req/sec, so one batch for the whole ingest run comfortably fits both
    limits. No-ops on an empty batch. Never logs `push_url` — it embeds an API key.
    """
    if not rows:
        return

    try:
        resp = requests.post(push_url, json=rows, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise PowerBIError(f"request failed: {exc}") from exc

    if not (200 <= resp.status_code < 300):
        raise PowerBIError(f"push failed with HTTP {resp.status_code}")

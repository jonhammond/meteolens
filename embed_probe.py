"""One-shot diagnostic for the service-principal embed chain.

Prints ONLY step outcomes, HTTP status codes, and upstream error codes.
Never prints tokens, secrets, or full response bodies.
"""

import os
import re
import sys

import requests

TIMEOUT = 10

VARS = (
    "POWERBI_TENANT_ID",
    "POWERBI_CLIENT_ID",
    "POWERBI_CLIENT_SECRET",
    "POWERBI_WORKSPACE_ID",
    "POWERBI_REPORT_ID",
)


def main():
    print("== Step 0: environment ==")
    missing = []
    for name in VARS:
        value = os.environ.get(name, "")
        ok = bool(value.strip())
        print(f"  {name}: {'set' if ok else 'MISSING/BLANK'}")
        if not ok:
            missing.append(name)
    if missing:
        print("RESULT: fix the missing vars above and re-run.")
        return 1

    tenant = os.environ["POWERBI_TENANT_ID"].strip()
    client_id = os.environ["POWERBI_CLIENT_ID"].strip()
    secret = os.environ["POWERBI_CLIENT_SECRET"].strip()
    workspace = os.environ["POWERBI_WORKSPACE_ID"].strip()
    report = os.environ["POWERBI_REPORT_ID"].strip()

    guid = re.compile(r"^[0-9a-fA-F-]{36}$")
    for label, val in (("tenant", tenant), ("client", client_id),
                       ("workspace", workspace), ("report", report)):
        if not guid.match(val):
            print(f"  WARNING: {label} id does not look like a GUID "
                  f"(length {len(val)}) — check for quotes/whitespace in .env")

    print("== Step 1: AAD client-credentials token ==")
    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
            "scope": "https://analysis.windows.net/powerbi/api/.default",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        body = resp.text
        codes = sorted(set(re.findall(r"AADSTS\d+", body)))
        err = ""
        try:
            err = resp.json().get("error", "")
        except ValueError:
            pass
        print(f"  FAILED: HTTP {resp.status_code}, error={err!r}, "
              f"codes={codes or 'none found'}")
        print("RESULT: AAD auth failed — usually a wrong tenant id, wrong "
              "client id, or a mis-pasted client secret.")
        return 1
    token = resp.json()["access_token"]
    print(f"  OK (token acquired, {len(token)} chars, not shown)")
    headers = {"Authorization": f"Bearer {token}"}

    print("== Step 2: GET report in workspace ==")
    resp = requests.get(
        f"https://api.powerbi.com/v1.0/myorg/groups/{workspace}/reports/{report}",
        headers=headers,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        code = ""
        try:
            code = resp.json().get("error", {}).get("code", "")
        except ValueError:
            pass
        print(f"  FAILED: HTTP {resp.status_code}, error code={code!r}")
        print("RESULT: AAD auth works but Power BI rejects the service "
              "principal — usually tenant settings not propagated yet "
              "(~15 min) or missing workspace access, or a wrong "
              "workspace/report id.")
        return 1
    body = resp.json()
    dataset_id = body.get("datasetId", "")
    print(f"  OK (report name={body.get('name')!r}, datasetId={dataset_id})")

    print("== Step 3: GenerateToken ==")
    resp = requests.post(
        "https://api.powerbi.com/v1.0/myorg/GenerateToken",
        headers=headers,
        json={
            "reports": [{"id": report}],
            "datasets": [{"id": dataset_id}],
            "targetWorkspaces": [{"id": workspace}],
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        code = detail = ""
        try:
            err = resp.json().get("error", {})
            code = err.get("code", "")
            detail = err.get("message", "")
        except ValueError:
            pass
        print(f"  FAILED: HTTP {resp.status_code}, error code={code!r}, "
              f"message={detail!r}")
        print("RESULT: GenerateToken rejected — commonly capacity/tenant "
              "restrictions or the 'Embed content in apps' setting.")
        return 1
    tok = resp.json()
    print(f"  OK (embed token acquired, {len(tok.get('token', ''))} chars, "
          f"not shown; expiration={tok.get('expiration')})")
    print("RESULT: the whole chain works — the Flask 503 should be re-tested.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Delete expired loadtest namespaces. Standalone, stdlib only (python:3.12-alpine).

Runs in-cluster as a CronJob; the script is mounted from a ConfigMap, so it must not
import the `loadtest` package.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

# Mirrors engine/cli/src/loadtest/limits.py (guarded by test_constants_match_cli_limits).
MAX_DURATION_S = 900
TTL_BUFFER_S = 900
PROVISION_TIMEOUT_S = 300
SMOKE_DURATION_S = 15
# Fallback when expires-at is missing: the longest lifetime the CLI ever stamps.
MAX_LIFETIME_S = MAX_DURATION_S + SMOKE_DURATION_S + PROVISION_TIMEOUT_S + TTL_BUFFER_S
# Mirrors the label/annotation keys the CLI stamps on every test namespace.
MANAGED_LABEL = "loadtest.platform/managed"
EXPIRES_ANNOTATION = "loadtest.platform/expires-at"

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
TIMEOUT_S = 30


def _created_epoch(meta: dict) -> int | None:
    raw = meta.get("creationTimestamp")
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return int(parsed.replace(tzinfo=UTC).timestamp())


def _is_expired(meta: dict, now: int) -> bool:
    try:
        return int(meta["annotations"][EXPIRES_ANNOTATION]) < now
    except (KeyError, TypeError, ValueError):
        pass
    created = _created_epoch(meta)
    if created is None:
        return False
    return now - created > MAX_LIFETIME_S


def expired_namespaces(items: list[dict], now: int) -> list[str]:
    names = []
    for item in items:
        meta = item.get("metadata") or {}
        if (meta.get("labels") or {}).get(MANAGED_LABEL) != "true":
            continue
        if (item.get("status") or {}).get("phase") == "Terminating":
            continue
        if _is_expired(meta, now):
            names.append(meta["name"])
    return names


def _read(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def _default_opener() -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open


def main(opener=None, now: int | None = None, token: str | None = None, env=None) -> int:
    env = os.environ if env is None else env
    host = env.get("KUBERNETES_SERVICE_HOST")
    port = env.get("KUBERNETES_SERVICE_PORT")
    base = f"https://{host}:{port}" if host and port else "https://kubernetes.default.svc"
    token = token if token is not None else _read(f"{SA_DIR}/token")
    opener = opener or _default_opener()
    now = int(datetime.now(UTC).timestamp()) if now is None else now
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    query = urllib.parse.urlencode({"labelSelector": f"{MANAGED_LABEL}=true"})
    req = urllib.request.Request(f"{base}/api/v1/namespaces?{query}", headers=headers)
    with opener(req, timeout=TIMEOUT_S) as resp:
        items = json.load(resp).get("items", [])

    failures = 0
    for name in expired_namespaces(items, now):
        url = f"{base}/api/v1/namespaces/{urllib.parse.quote(name, safe='')}"
        try:
            with opener(
                urllib.request.Request(url, method="DELETE", headers=headers), timeout=TIMEOUT_S
            ):
                pass
            print(f"deleted namespace {name}", flush=True)
        except (urllib.error.URLError, OSError) as exc:
            failures += 1
            print(f"failed to delete namespace {name}: {exc}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

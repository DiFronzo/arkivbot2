#!/bin/bash
# Liveness probe for the arkivbot-web job (see health-check-script in jobs.yaml).
#
# Exists so the job never depends on curl being present in the image. Exits 0
# only when the status endpoint answers; before the first archiving run the
# server synthesises "unknown", so this is healthy from the very first pod.
set -euo pipefail

PORT="${PORT:-8080}"

exec python3 - "$PORT" <<'PY'
import sys
import urllib.request

port = sys.argv[1]
with urllib.request.urlopen(f'http://127.0.0.1:{port}/status', timeout=5) as response:
    if response.status != 200:
        raise SystemExit(f'unhealthy: HTTP {response.status}')
    response.read(64)
PY

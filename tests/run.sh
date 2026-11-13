#!/usr/bin/env bash
# The generator's checks: no Docker needed (with docker compose installed the rendered files
# also go through `docker compose config`; set CADDY_BIN to a caddy binary to validate the sites too).
set -euo pipefail
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/test_apps.py"

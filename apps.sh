#!/usr/bin/env bash
# The app generator. Run from anywhere; it works on the server folder it sits in (see README.md).
set -euo pipefail
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/apps.py" "$@"

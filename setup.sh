#!/bin/sh
set -eu
cd "$(dirname "$0")"
VOICE_BOOTSTRAP_PYTHON="${VOICE_BOOTSTRAP_PYTHON:-python3}"
exec "$VOICE_BOOTSTRAP_PYTHON" scripts/bootstrap.py "$@"

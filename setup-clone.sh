#!/bin/sh
set -eu
cd "$(dirname "$0")"
VOICE_CLONE_BOOTSTRAP_PYTHON="${VOICE_CLONE_BOOTSTRAP_PYTHON:-python3}"
exec "$VOICE_CLONE_BOOTSTRAP_PYTHON" scripts/bootstrap_clone.py "$@"

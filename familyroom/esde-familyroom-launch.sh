#!/usr/bin/env bash
set -euo pipefail
if [[ "${ESDE_SYNC_TEST_MODE:-}" == "1" ]]; then
  WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  exec python3 "$WORK_DIR/esde-sync" --page=dropbox
fi
exec "$HOME/.local/bin/esde-sync" --page=dropbox

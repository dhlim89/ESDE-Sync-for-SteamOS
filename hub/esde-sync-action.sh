#!/usr/bin/env bash
set -euo pipefail
if [[ "${ESDE_SYNC_TEST_MODE:-}" == "1" ]]; then
  WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  case "${1:-}" in
    android) exec python3 "$WORK_DIR/esde-sync" --page=android ;;
    family) exec python3 "$WORK_DIR/esde-sync" --page=dropbox ;;
    *) exit 2 ;;
  esac
fi
case "${1:-}" in
  android) exec "$HOME/.local/bin/esde-sync" --page=android ;;
  family) exec "$HOME/.local/bin/esde-sync" --page=dropbox ;;
  *) exit 2 ;;
esac

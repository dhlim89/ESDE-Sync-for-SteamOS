#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_HOME="${ESDE_SYNC_APP_HOME:-$HOME/.local/share/esde-sync}"
BIN_DIR="${ESDE_SYNC_BIN_DIR:-$HOME/.local/bin}"
DESKTOP_DIR="${ESDE_SYNC_DESKTOP_DIR:-$HOME/.local/share/applications}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/esde-sync"

mkdir -p "$APP_HOME/web" "$BIN_DIR" "$DESKTOP_DIR"

# Stop any old ES-DE Sync backend so newly installed code is actually used.
pkill -f "$APP_HOME/esde-sync-gui.py" 2>/dev/null || true
rm -f "$STATE_DIR/gui-port"

install -m 755 "$BASE_DIR/esde-sync-engine" "$APP_HOME/esde-sync-engine"
install -m 755 "$BASE_DIR/esde-sync-gui.py" "$APP_HOME/esde-sync-gui.py"
cp -r "$BASE_DIR/web/." "$APP_HOME/web/"

{
  printf '#!/usr/bin/env bash\n'
  printf 'export ESDE_SYNC_APP_HOME=%q\n' "$APP_HOME"
  printf 'exec python3 %q\n' "$APP_HOME/esde-sync-gui.py"
} > "$BIN_DIR/esde-sync"
chmod +x "$BIN_DIR/esde-sync"

cat > "$DESKTOP_DIR/esde-sync.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ES-DE Sync
Comment=Legion Go S 기준으로 Android ES-DE 라이브러리를 동기화합니다
Exec=$BIN_DIR/esde-sync
Icon=drive-removable-media
Terminal=false
Categories=Utility;Game;
StartupNotify=true
EOF
chmod +x "$DESKTOP_DIR/esde-sync.desktop"

echo "ES-DE Sync v0.37 설치 완료"
echo "앱 메뉴에서 ES-DE Sync를 실행하세요."

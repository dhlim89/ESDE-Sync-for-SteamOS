# ES-DE Sync v0.37

업데이트 정보는 GitHub Contents API에서 base64 디코딩 후 읽습니다. ZIP 다운로드 주소는 version.json의 url을 그대로 사용합니다. 자동 설치 없이 업데이트 버튼으로 설치합니다.

## SteamOS 설치

압축 해제한 esde-sync-v0.37 폴더에서 실행하세요:

```bash
chmod +x install.sh && ./install.sh
```

기본 설치 위치는 ~/.local/share/esde-sync, 실행 파일은 ~/.local/bin/esde-sync입니다. 기존 백엔드를 종료하고 gui-port를 지운 뒤 설치합니다. ~/.config/esde-sync/profiles는 변경하지 않습니다.

## 개발·검증

Python 3와 Bash가 필요합니다. 저장소 루트에서 `python3 tests/test_integration.py`를 실행하세요. 이 테스트에는 실제 GitHub API 접근이 필요합니다.

읽기 전용 홈 환경에서는 ESDE_SYNC_APP_HOME, ESDE_SYNC_BIN_DIR, ESDE_SYNC_DESKTOP_DIR, XDG_STATE_HOME, XDG_CONFIG_HOME을 쓰기 가능한 별도 경로로 지정할 수 있습니다. ESDE_SYNC_HEADLESS=1은 브라우저를 열지 않고 실제 HTTP 백엔드를 실행합니다. 일반 SteamOS 설치에는 이 옵션이 필요 없습니다.

USB 동기화에는 adb와 기존 기기 프로필이 필요합니다. 기기를 한 대씩 연결하세요. 원본 ROM·gamelist·미디어와 동기화 정책은 v0.36과 동일합니다. 이번 릴리스에서 실제 USB 기기 전송은 검증하지 않았습니다.

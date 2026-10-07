# v0.37 검증 및 배포

현재 클라우드에서 `python3 tests/test_integration.py`를 실행해 5개 통합 테스트가 통과했습니다. Python 컴파일 검사도 이 명령에서 실행합니다.

- 실제 localhost 서버와 HTTP `/api/state`, `/api/update` 호출.
- 로컬 Contents API 형식 응답: current=0.37/latest=0.38/available=true, latest=0.36이면 available=false.
- 실제 GitHub Contents API의 main/version.json: 검증 당시 version=0.36. 설치된 v0.37 서버에서 latest=0.36, available=false 확인.
- HTTP 404 다운로드 실패 및 SHA-256 불일치: HTTP 200 + ok=false JSON, update.log에 오류 기록.
- 잘못된 base64 메타데이터: 상태 및 업데이트 API가 JSON 오류로 응답.
- 두 번째 실행은 정상 종료하고 첫 백엔드와 포트 유지.
- 임시 설치본의 기존 0.36 백엔드 종료, stale gui-port 제거, 새 0.37 백엔드 실행.
- 올바른 ZIP의 체크섬 검증, 업데이트 helper의 실제 install.sh 실행, install exit=0, 백엔드 재실행.
- 테스트 프로필 내용이 설치 및 업데이트 후 바이트 단위로 동일.
- 최종 릴리스 ZIP의 CRC와 모든 파일이 src와 동일함을 확인. 엔진과 web 파일은 v0.36에서 변경하지 않음.

## 검증 범위

설치 테스트는 기본 홈이 읽기 전용인 클라우드에서 공식 설치 스크립트의 경로 옵션을 사용해 임시 디렉터리에 실행했습니다. SteamOS의 /home/deck 기본 경로 설치와 Brave 화면, 실제 USB 기기 전송은 이 테스트로 검증되지 않았습니다. 로컬 Legion v0.35의 정상 동작은 사용자가 전달한 기존 확인 기록입니다.

기존 GUI 소스의 기기 등록 함수는 re를 사용하지만 모듈 수준 import re가 없고, foreground 탐지는 dumpsys window만 사용합니다. 전달된 기존 개발 기록의 범용 foreground 탐지와 현재 v0.36 ZIP은 이 부분이 다릅니다. 이번 업데이트 조회 변경에는 포함하지 않았습니다. 알 수 없는 foreground에서는 기존 동기화 차단 동작을 유지합니다.

## 설치

ES-DE-Sync-v0.37.zip을 내려받아 압축 해제하고, esde-sync-v0.37 폴더에서 실행합니다:

```bash
chmod +x install.sh && ./install.sh
```

기존 기기 프로필은 보존됩니다. 사용자 동기화 라이브러리는 변경하지 않습니다.

## 배포

릴리스 ZIP과 version.json을 같은 PR에 포함했습니다. main에 병합되기 전에는 기존 사용자가 v0.37을 업데이트로 받지 않습니다. 자동 설치하지 않으며 업데이트 버튼을 눌러야 합니다.

기존 v0.35는 raw 메타데이터를 사용하므로 새 조회 방식의 혜택을 받으려면 첫 v0.37은 수동 설치가 필요할 수 있습니다. GitHub API는 인증 없는 요청에 사용량 제한이 있으며 실패하면 기존 JSON 오류 표시를 사용합니다.

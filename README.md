# ES-DE Sync v0.44

Android 기기 동기화와 Dropbox 공유 라이브러리를 단일 Python backend와 단일 Brave app 창에서 사용하는 통합 배포판입니다. 좌측 navigation으로 화면을 전환하며 공통 header에서 버전·업데이트 상태, footer에서 작업 상태·최근 결과를 확인합니다.

## 설치

압축을 해제한 `ES-DE-Sync-v0.44` 폴더에서 실행합니다.

```bash
chmod +x install.sh && ./install.sh
```

메뉴에는 `ES-DE Sync` 하나만 등록됩니다. Android/Dropbox desktop actions는 같은 통합 앱의 화면을 선택합니다. 기존 Hub/Dropbox 진입 파일은 단일 launcher 호출 wrapper로만 유지하며 독립 backend나 별도 창을 생성하지 않습니다.

installer는 파일을 staging·검증한 뒤 교체하고, 실패하면 코드·launcher·메뉴를 rollback합니다. 기존 profile, Dropbox 선택 설정, runtime/state 및 실제 라이브러리를 migration하거나 초기화하지 않습니다. 신규 설치에서 새 Android 기기를 등록하려면 기존 정책에 따라 검증된 기본 profile template이 필요합니다.

## 동기화 구조와 정책

- `esde-sync`: backend readiness와 기존 창 session을 확인하는 유일한 launcher.
- `esde-sync-gui.py`: 단일 HTTP backend, 공통 상태·업데이트 API.
- `services/android.py`: 기존 device/profile/foreground 정책과 `esde-sync-engine`의 adapter.
- `services/dropbox.py`: 기존 Dropbox 미러링·삭제·gamelist 변환 정책.
- `services/jobs.py`: Android sync, Dropbox update, app update의 상호 배타 예약·진행·결과 관리.
- `web/`: 단일 frontend. 화면 전환 시 선택·진행·최근 결과 유지.

v0.43은 system-level `alternativeEmulator`를 기기별 설정으로 보존합니다. Android에 태그가 존재하면 XML subtree 전체를 보존하고, 없을 때만 Legion 값을 사용합니다. 게임별 `altemulator`와 runtime merge 정책은 유지합니다. Android gamelist의 runtime merge는 Legion 원본을 수정하지 않고 Android-bound 임시 사본에 적용합니다. Dropbox gamelist 변환은 별도 정책이며 GB/GBC의 정확한 `Sameboy (Standalone)` 값을 `My OldBoy! (Standalone)`으로 변환합니다. ROM/media는 각 대상에 기존 미러링 규칙으로 반영됩니다.

Dropbox 선택 해제 시스템과 Legion에서 제거된 파일은 Dropbox에서도 삭제됩니다. 실행 전 선택을 확인하세요. 세 작업은 동시에 실행되지 않으며 충돌 요청은 HTTP 409로 차단됩니다. 직접 실행하는 원본 engine에는 새로운 잠금을 추가하지 않았고 통합 backend는 외부 engine 실행을 감지합니다.

## 기존 데이터 위치

- Android profiles: `~/.config/esde-sync/profiles`
- 앱 state/log: `~/.local/state/esde-sync`
- Dropbox 선택: `~/.config/esde-familyroom-updater/systems.conf`
- Dropbox state/log: `~/.local/state/esde-familyroom-updater`
- Dropbox 라이브러리: `~/Dropbox/ES-DE Sync`
- Legion ROM/media: `~/Emulation/roms`, `~/Emulation/tools/downloaded_media`
- Legion gamelist: `~/ES-DE/gamelists`

## 앱 업데이트와 lifecycle

기존 SHA-256 검증, detached helper, transactional staging/rollback을 유지합니다. 앱 업데이트는 Android/Dropbox 작업 중 차단됩니다. 업데이트 후 새 창을 여는 기존 방식은 유지하며 same-window reconnect, fixed port 재사용, startup health 실패 rollback 확장은 포함하지 않습니다.

일반 재실행은 launcher 잠금과 UI heartbeat로 기존 session을 재사용합니다. `wmctrl`이 있으면 창 활성화를 시도하며 Wayland에서의 강제 focus는 별도입니다. 읽기 전용 lifecycle test mode는 설치 경로와 사용자 데이터를 분리해 작업본을 검증하는 용도로 유지합니다.

## 과거 v0.43 검증 기록

- runtime merge 19/19, system default 포함 fixture 28/28: PASS.
- profile/foreground/Dropbox/job manager/frontend/lifecycle 및 격리 transactional install/rollback: PASS.
- 실제 v0.42 → v0.43 설치 migration: PASS.
- Pocket Air Mini 8개 시스템 실제 Android sync: PASS. Arcade ROM 10개 갱신, N64 media 1개 추가, 삭제·실패 0개.
- Android runtime 480개 필드 보존 및 승인된 Legion fallback 12개 필드 반영. Legion master 불변.
- NES `FCEUmm`, SNES `Snes9x 2010`, N64 `M64Plus FZ (Standalone)`, Arcade `FB Alpha 2012` 및 게임별 `FinalBurn Neo` 10개 유지.
- 위 NES/SNES/N64/Arcade 대표 게임 실제 실행: 사용자 검증 및 PASS 승인.

v0.43 검증 결과와 `.review/` 아래 스냅샷·백업·로그·release validation은 역사 기록으로 보존합니다. 위 PASS는 당시 v0.43 결과이며 v0.44의 실제 기기 설치·동기화 검증을 의미하지 않습니다.

## v0.44 유지보수와 공개 소스 구조

개발·수정·테스트 환경은 Lenovo Legion Go S의 SteamOS입니다. Windows용 프로젝트와 독립적으로 개발·유지보수합니다. v0.44는 버전 표시, 업데이트 저장소 주소, KDE 메뉴 분류와 배포 구조를 정리하며 v0.43 동기화 엔진 및 ROM/gamelist/downloaded_media/profile 정책을 유지합니다.

공개 저장소: https://github.com/dhlim89/ESDE-Sync-for-SteamOS

최신 편집 가능한 소스는 main의 프로젝트 루트에서 관리합니다. 이번 버전에서는 src/ 또는 app/로 이동하지 않습니다.

```text
README.md
install.sh
install-transaction.py
esde-sync
esde-sync-engine
esde-sync-gui.py
update-helper.py
esde-sync.desktop
services/
web/
hub/
familyroom/
tests/
releases/
version.json
.gitignore
```

자동 업데이트는 `https://api.github.com/repos/dhlim89/ESDE-Sync-for-SteamOS/contents/version.json?ref=main`에서 metadata를 조회합니다. 기존 ZIP 다운로드 방식과 version.json 호환성을 유지하며, ZIP의 SHA-256을 확인한 뒤 포함된 install.sh로 설치합니다. version.json의 version/file/url/sha256/notes/channel 형식을 유지합니다.

v0.44 ZIP은 `releases/ES-DE-Sync-v0.44.zip`으로 새로 배포하고 이미 배포된 v0.43 및 과거 release ZIP은 덮어쓰지 않습니다. version.json은 최종 검증 후 준비하고 GitHub 게시 및 실제 설치는 별도 승인 후 진행합니다.

`.review/`, `dist/`, `__pycache__/`, `*.pyc`, 로컬 테스트 산출물과 사용자 데이터는 main 공개 대상에서 제외합니다. 배포 ZIP에는 기존 20개 제품 파일만 포함하며 tests와 검증 도구는 제외합니다.

기존 tests에는 과거 버전 고정값과 `.review/` 결과 저장 코드가 남아 있습니다. 역사 기록 보존을 위해 그대로 일괄 실행하지 않습니다. v0.44 전용 검증은 `python3 -B tests/test_release_v044.py`로 실행하며 결과는 `dist/v044-validation/` 및 `/tmp`의 격리 fixture에 저장합니다. 패키지 생성은 `python3 -B tests/build_release_v044.py`로 수행하며 기존 ZIP을 덮어쓰지 않습니다. 실제 동기화는 실행하지 않습니다.

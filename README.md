# ES-DE Sync for SteamOS v0.45

Android 기기 동기화와 Dropbox 공유 라이브러리를 단일 Python backend와 단일 Brave app 창에서 사용하는 통합 배포판입니다. 좌측 navigation으로 화면을 전환하며 공통 header에서 버전·업데이트 상태, footer에서 작업 상태·최근 결과를 확인합니다.

## 설치

압축을 해제한 `ES-DE-Sync-v0.45` 폴더에서 실행합니다.

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

## v0.45 ROM 무결성 및 gamelist 보존

SteamOS는 ROM, media 및 일반 gamelist 메타데이터의 원본입니다. Android의 같은 경로 게임에 기록된 `playcount`, `playtime`, `lastplayed`와 기기별 emulator 설정은 기존 병합 규칙에 따라 보존합니다. system-level `alternativeEmulator`가 Android에 있으면 그 값을 유지하며, Arcade 게임별 `altemulator`도 그대로 유지합니다. 매핑되지 않은 Android-only 게임은 미러 정책에 따라 제거됩니다.

ROM은 경로와 크기를 비교한 뒤 SHA-256으로 내용을 확인합니다. 변경 ROM은 같은 디렉터리의 임시 파일로 전송하고, 원본 백업·임시 파일·최종 파일을 크기와 SHA-256으로 검증합니다. 실패 시 백업 사본으로 복구를 시도하며, 복구가 확정되지 않으면 트랜잭션 잔재를 보존하고 다음 실행을 차단합니다. Android FUSE의 rename 원자성이나 전원 손실 내구성까지 보장한다고 간주하지 않습니다.

원격 인덱스는 `ESDE_INDEX_V2` 길이 프레임을 사용합니다. 잘림·파싱 오류·불완전한 목록이나 SteamOS 원본 디렉터리 조회 실패가 있으면 해당 시스템의 관련 전송과 삭제를 차단합니다. 존재하는 정상 빈 ROM 원본 디렉터리는 유효한 빈 원본으로 처리하여 mirror 정책에 따른 Android ROM 삭제를 허용합니다.

ROM 파일명 변경 시 runtime을 이어받으려면 기기 serial별 JSON 매핑을 선택적으로 둘 수 있습니다.

```text
~/.config/esde-sync/profiles/<ADB serial>.runtime-map.json
```

스키마는 `schema_version: 1`, 일치하는 `device_serial`, 시스템별 `from` → `to` 경로 배열입니다. 예를 들어 `./Old Name.gb`에서 `./New Name.gbc`로 옮길 때 명시적 경로 관계가 있을 때만 적용합니다. SHA-256, 게임명 유사도 또는 파일 크기로 자동 연결하지 않습니다. SteamOS에 대상 경로가 있고 Android에 원본 경로가 남아 있을 때 `playcount`, `playtime`, `lastplayed`만 복사합니다. 대상 runtime 충돌, 잘못된 스키마 또는 다른 serial은 안전하게 거부됩니다. 매핑이 없으면 기존 경로 기준 병합을 사용합니다.

`downloaded_media`는 현재 경로와 파일 크기로 비교합니다. 동일 경로·동일 크기 media의 내용 SHA-256은 검사하지 않습니다.

GUI에서 새 기기를 등록할 때 이미 있는 프로필은 그대로 사용합니다. 새 프로필은 동일한 `DEVICE_MANUFACTURER`와 `DEVICE_MODEL`을 가진 기존 프로필의 기기별 emulator 기본값을 재사용합니다. 일치하는 프로필이 없거나 같은 모델의 설정이 서로 다르면 개인 serial을 기준으로 고르지 않고 시스템 선택과 기기 식별 정보만 담은 기본 프로필을 만듭니다. 저장 파일명과 이후 기기 선택은 계속 실제 ADB serial을 사용합니다.

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

## v0.45 유지보수와 공개 소스 구조

개발·수정·테스트 환경은 SteamOS입니다. Windows용 프로젝트와 독립적으로 개발·유지보수합니다. v0.45는 v0.44의 GUI, Dropbox, updater 및 설치 구조를 유지하면서 Android gamelist 누락 처리, SHA-256 ROM 변경 감지, 복구 가능한 ROM 교체와 명시적 runtime 경로 매핑을 추가합니다.

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

v0.45 ZIP은 `releases/ES-DE-Sync-v0.45.zip`으로 별도 배포합니다. 기존 v0.44 및 이전 release ZIP은 덮어쓰지 않습니다. stable `version.json`은 검증된 v0.45 ZIP의 SHA-256을 확정한 뒤 별도 게시 단계에서 갱신합니다.

`.review/`, `dist/`, `__pycache__/`, `*.pyc`, 로컬 테스트 산출물과 사용자 데이터는 main 공개 대상에서 제외합니다. 배포 ZIP에는 기존 20개 제품 파일만 포함하며 tests, 테스트 계획, 가짜 ADB, 실기기 증거, 프로필과 runtime-map 설정은 제외합니다.

v0.45 제품 ZIP은 `python3 -B tests/build_release_v045.py --output <새 격리 경로>`로 생성하고 `python3 -B tests/test_release_v045.py --archive <ZIP 경로>`로 검증합니다. 빌더는 고정된 제품 파일 목록을 사용하고 기존 출력 파일을 덮어쓰지 않습니다. `version.json`의 stable 갱신과 게시, 실제 설치는 별도 단계입니다.

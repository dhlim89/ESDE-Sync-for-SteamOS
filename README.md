# ES-DE Sync for SteamOS

ES-DE Sync for SteamOS는 **Legion Go S의 SteamOS를 기준 기기(source of truth)** 로 사용하여 Android 기반 에뮬레이션 기기와 Dropbox 공유 라이브러리에 ES-DE 데이터를 동기화하기 위한 개인 프로젝트입니다.

현재 SteamOS용 프로젝트와 Windows용 프로젝트는 서로 독립적으로 개발·유지보수합니다.

- SteamOS: **ESDE-Sync-for-SteamOS**
- Windows: https://github.com/dhlim89/ESDE-Sync-for-Windows

## Current release

현재 안정 버전은 **v0.43**입니다.

업데이트 메타데이터는 `version.json`에서 관리하며, 기존 설치본과의 호환성을 위해 배포 ZIP은 현재 `releases/` 디렉터리에서도 제공합니다.

## Main features

- Legion Go S의 ROM을 기준으로 선택한 Android 기기의 ROM 동기화
- ES-DE `gamelist.xml` 변환 및 동기화
- Android 기기의 플레이 기록 보존
  - `playcount`
  - `playtime`
  - `lastplayed`
- 기기별 대체 에뮬레이터 설정 처리
- `downloaded_media` 미러링
- Dropbox 공유 라이브러리 동기화
- 프로그램 내 자동 업데이트

## Development environment

SteamOS용 ES-DE Sync의 개발·수정·테스트는 **Lenovo Legion Go S의 SteamOS 환경에서 직접 수행**합니다.

Windows용 ES-DE Sync는 별도 저장소와 별도 개발 환경을 사용합니다. 두 프로젝트의 소스와 배포 체계는 서로 독립적으로 관리합니다.

## Installation

배포 ZIP을 내려받아 압축을 푼 뒤, 해당 디렉터리에서 다음 명령을 실행합니다.

```bash
chmod +x install.sh && ./install.sh
```

설치 후 사용자 설정과 기기별 프로필은 사용자 홈 디렉터리 아래에 보존됩니다.

## Update

프로그램은 GitHub의 `version.json`을 확인하여 새 버전을 감지합니다.

현재 업데이트 메타데이터:

```text
https://api.github.com/repos/dhlim89/ESDE-Sync-for-SteamOS/contents/version.json?ref=main
```

배포 ZIP은 `version.json`에 기록된 URL과 SHA-256 체크섬으로 검증합니다.

## Repository layout

```text
.
├── README.md
├── releases/       # 기존 설치본 호환을 위한 배포 ZIP
└── version.json    # 자동 업데이트용 메타데이터
```

v0.44부터는 실제 배포에 사용되는 편집 가능한 소스도 저장소에서 추적할 수 있도록 구조를 정리할 예정입니다. 과거 릴리스와 검증 기록은 임의로 삭제하거나 덮어쓰지 않습니다.

## Release policy

- 이미 배포한 ZIP은 같은 버전 번호로 교체하지 않습니다.
- 새 변경 사항은 다음 버전으로 배포합니다.
- `version.json`의 SHA-256과 실제 ZIP의 체크섬을 일치시킵니다.
- 동기화 기능 변경과 저장소/배포 구조 변경은 가능한 한 분리하여 검증합니다.
- 실제 Legion Go S 설치본을 최종 검증 기준으로 사용합니다.

## Status

이 저장소는 개인 환경에 맞춰 지속적으로 개발 중입니다. 다른 환경에서는 경로, ES-DE 구성, Android 기기 설정에 따라 별도 조정이 필요할 수 있습니다.

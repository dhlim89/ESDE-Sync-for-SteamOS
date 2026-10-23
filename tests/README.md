# ES-DE Sync 테스트 및 릴리스 검증

## v0.45 회귀 테스트

```bash
python3 -B tests/test_sync_integrity.py
python3 -B tests/device_event_log.py --self-test
python3 -B tests/test_gui_profiles.py
```

테스트는 가짜 ADB와 임시 파일만 사용합니다. 기본 fixture는 시스템 임시 디렉터리에 생성되고 종료 시 제거됩니다. 격리된 기존 테스트 루트 아래에서 실행하려면 `ESDE_SYNC_TEST_ROOT`에 전용 디렉터리를 지정할 수 있습니다. 저장소 자체는 테스트 fixture 위치로 허용되지 않습니다.

검사 범위에는 gamelist 상태·길이 프레이밍, XML 오류, SHA-256 비교, ROM 백업·교체·복구, 중단·저장공간 오류, 원격 인덱스 완전성, 시스템 단위 삭제 차단, runtime-map 충돌·serial 격리, runtime 및 emulator 병합 회귀, 기기 제조사·모델 기반 프로필 템플릿 선택이 포함됩니다. 64 MiB 스트림 측정은 호스트 fixture 기준이며 Android 성능이나 FUSE rename 원자성을 보장하지 않습니다.

## v0.45 릴리스 패키지 검증

```bash
python3 -B tests/build_release_v045.py --output /tmp/ES-DE-Sync-v0.45-check.zip
python3 -B tests/test_release_v045.py --archive /tmp/ES-DE-Sync-v0.45-check.zip
```

빌더는 명시된 제품 파일 20개만 포함하고 테스트 파일·가짜 ADB·테스트 계획·프로필·runtime-map·사용자 ROM/media를 제외합니다. ZIP은 고정 메타데이터로 결정적으로 생성하며 기존 파일은 덮어쓰지 않습니다. 검증기는 ZIP 구조·해시·권한, 신규 설치, v0.44에서 v0.45 업그레이드, rollback 및 사용자 설정 보존을 임시 HOME에서 확인합니다.

stable `version.json`은 변경하지 않습니다. 새 ZIP의 SHA-256을 확인한 후 별도 게시 절차에서 version metadata를 준비합니다.

## v0.44 안정판 기준 검증

v0.44의 builder와 공개 검증은 v0.45 개발 checkout의 파일이 아닌 깨끗한 v0.44 기준 사본에서 실행합니다. 기준 ZIP 및 v0.44 검증 코드는 수정하지 않습니다. 기준 사본에 필요한 v0.43 이전 ZIP이 빠져 있다면 검증용 임시 사본에만 기존 아카이브를 제공하고 원본 스냅샷은 변경하지 않습니다.

## 실기기 검증 자료

실기기 계획, serial, 로그, 백업 manifest 및 기타 증거는 공개 제품 패키지에 포함하지 않습니다. 공개 회귀 테스트는 실제 Android 기기와 사용자 데이터를 사용하지 않습니다.

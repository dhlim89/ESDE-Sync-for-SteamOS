# v0.44 공개 release 테스트

공개 tests/는 v0.44부터 현재 checkout의 제품 소스와 releases/만으로 재현 가능한 release 검증용입니다. 공개 파일은 다음 세 개입니다.

- build_release_v044.py
- test_release_v044.py
- README.md

과거 개발 테스트, baseline 및 검증 결과는 로컬 역사 자료로 보존하며 공개 tests/에 포함하지 않습니다. 과거 개발 과정의 검증 기록도 저장소에 게시하지 않습니다.

## 실행

SteamOS/Linux에서 Python 3와 bash가 필요합니다. 저장소 루트에서 실행합니다.

```bash
python3 -B tests/test_release_v044.py
```

다음 두 공개 release artifact가 필요합니다. 테스트 중 다운로드하지 않습니다.

```text
releases/ES-DE-Sync-v0.43.zip
releases/ES-DE-Sync-v0.44.zip
```

테스트는 Python/shell syntax, 20개 제품 파일 구성, executable mode, CRC, 압축 해제, 고정 SHA-256과 파일 크기, 임시 v0.44 build의 기존 ZIP 일치, 기존 ZIP 덮어쓰기 방지를 확인합니다. 이어 신규 설치, v0.43 → v0.44 업그레이드, profile/settings 및 가상 라이브러리 보존, 교체 후 실패 시 rollback을 검증합니다.

실제 사용자 HOME, Android 기기, Dropbox 데이터 또는 네트워크를 사용하지 않습니다. backend나 브라우저를 실행하지 않고 실제 동기화도 실행하지 않습니다. 모든 설치는 tempfile로 생성한 HOME fixture에서만 수행합니다. 임시 ZIP, HOME, 로그는 성공·실패 시 TemporaryDirectory가 정리하며 결과는 표준 출력으로만 보고합니다.

별도 ZIP 생성이 필요하면 지정한 새 경로에 생성할 수 있습니다.

```bash
python3 -B tests/build_release_v044.py --output /tmp/ES-DE-Sync-v0.44-check.zip
```

이 명령은 지정 경로에 파일을 남기므로 확인 후 직접 삭제해야 합니다. 기존 파일이 있으면 덮어쓰지 않고 실패합니다. --output을 생략하면 releases/ES-DE-Sync-v0.44.zip을 대상으로 하며, 기존 release가 있는 checkout에서는 실패하는 것이 정상입니다.

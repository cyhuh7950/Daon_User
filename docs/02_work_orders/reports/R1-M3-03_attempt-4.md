DIRECT_IMPLEMENTATION_COMPLETED | R1-M3-03-I001 | Fixture Marker 정리·Installer GUI 선택 보정·교차 플랫폼 Coverage·Exact Commit 증거 결속 | Test 전용 Guard·Cargo default-run·Pipe/Fail-close Test·Windows GUI·ARM64 Gate | Windows/ARM64 7범주 Gate PASS·Python 32/32·ARM64 Coverage 85.95%·설치 GUI/Sidecar PASS | 미해결 없음, DB Migration N/A | PR 검토·병합 후보

# R1-M3-03 Attempt 4 직접 구현 보고

## 판정

`DIRECT_IMPLEMENTATION_COMPLETED` — 신산님 승인 후 어울1이 단독 인수하여 Attempt 3의 마지막 Important 결함을 보정했다.

## 판단 이유

- 실제 Production Manager 오류 Fixture 종료 후 Marker가 존재하지 않아야 한다는 Assertion을 먼저 추가해 `13 passed, 1 failed` RED를 확인했다.
- Process 종료 확인 직후 Marker를 삭제하고, Test가 중간 종료되더라도 `Drop`에서 동일 Marker만 제거하는 Test 전용 Guard를 추가했다.
- 기존 `%TEMP%` Marker 14개와 직접 구현 재현 중 생성된 `C:\tmp` Marker 2개를 이름·부모 경로·Reparse 여부 검증 후 정확히 제거했다. 다른 Temp 파일은 삭제하지 않았다.
- 최종 보호 래퍼는 Rust Unit `14/14`, Contract `3/3` PASS이며 실행 뒤 두 Temp Root의 Marker는 0개다.
- 최초 Installer 검증에서 다중 Cargo Binary 중 테스트용 `local-service-lifecycle-host.exe`가 Application으로 선택된 결함을 발견했다. 기본 실행 대상 누락을 RED로 고정하고 `default-run = "daon-user-desktop"` 한 줄로 보정했다.
- 수정 Installer Build 로그는 `Built application at ...\\daon-user-desktop.exe`를 명시했다. 설치 후 실제 `Daon 사용자 프로그램` 창과 `로컬 서비스가 준비되었습니다.` 상태를 확인했고 정상 닫기 후 GUI·Sidecar Process는 0개다.
- ysna-server ARM64에서 최초 Python Unit 27/27은 통과했으나 Coverage가 78.92%로 정책 85%에 미달했다. 정책을 낮추지 않고 실제 Parent Pipe와 EOF·Timeout·잘못된 Instance/Token Fail-close Test를 추가해 32/32, 85.95%로 통과시켰다.
- ysna-server Exact SHA `8a9a934b4ae8e69a51ad3f630ba3eeae46458d12`에서 Node Lint·독립성·Node Unit 25/25·Desktop Build, Python Lint·Type·Unit/Coverage·Compile, Rust 1.97.1 Test와 통합 7범주 Quality Gate가 모두 통과했다.
- Source Manifest는 승인 기준 `bd13f1694623ca1225415c0f157ef88f94df6f38`과 최종 구현 Commit `8a9a934b4ae8e69a51ad3f630ba3eeae46458d12`을 비교하고 Git Commit Blob 자체를 Hash하는 Exact Commit 모드로 재생성했다.

## 검증 결과

| 항목 | 결과 |
| --- | --- |
| Marker Cleanup RED | Rust Unit 13/14, 새 Cleanup Assertion 1건 의도한 실패 |
| Marker Cleanup GREEN | Rust Unit 14/14, Contract 3/3 PASS |
| 현재 Marker 정리 | 16개 정확 제거, 잔존 0 |
| 최종 전체 Gate | lint 6, type 3, unit 7, contract 2, build 6, security 3, independence 1 모두 PASS |
| Installer Binary Selection RED→GREEN | 대상 Test 0/1 의도한 실패 → Desktop Shell 12/12 PASS, Cargo `default_run=daon-user-desktop` |
| 최종 Gate SHA | Windows·ysna-server 모두 `8a9a934b4ae8e69a51ad3f630ba3eeae46458d12` |
| Exact Source Binding | 46개 Blob, Entries `927E7B41...143C`, Binary Patch `D507AF00...8E74` |
| Windows 설치형 GUI | Installer SHA-256 `19AF8411393149061F8B7CAFBDE25FE77F0FADE3CF18E5A32B85841D8195C97B`, 19,380,724 bytes; GUI 제목·Local Service Ready 확인 |
| 종료 수명주기 | Installer 창 0, GUI 창 0, 관련 Process 0 |
| ysna-server ARM64 Python | 32/32 PASS, Coverage 85.95%, Lint·Type·Compile PASS |
| ysna-server ARM64 Rust | Rust/Cargo 1.97.1, Cargo Test Exit 0 |
| ysna-server 통합 Gate | lint 6, type 3, unit 7, contract 2, build 6, security 3, independence 1 모두 PASS, Failures 0 |
| 생성물 Cleanup | `.coverage`, Desktop `dist`, Tauri `gen`·staged Sidecar·Fixture Marker 잔존 0 |

## 영향 범위

- Production Parser·Manager·Frontend·API·DB 동작은 변경하지 않았다.
- 변경은 Windows Test Fixture Marker 수명주기, Cargo Installer 기본 Binary 선택과 Exact Commit 증거 생성에 한정했다.
- Token·Credential 값은 로그·보고·증거에 기록하지 않았다.
- DB Migration은 `N/A`다.

## 후속

- PR 검토·병합

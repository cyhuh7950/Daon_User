# R1-M5-03-C01 Local-private 저장 보안·Metadata 계약 보완 작업지시서

## 판정

`R1-M5-03`의 SQLCipher·sqlite-vec·AEAD File Store·Windows Credential Manager와 설치형 재시작/Key 철회 검증은 성공했다. 그러나 원 작업지시서가 명시한 Object Metadata, Versioned 암호문 Header, TOCTOU 경로 방어와 입력 경계 일부가 구현되지 않아 `VERIFYING → CORRECTION_REQUIRED`로 전환한다. 이는 어울1의 독립 검토에서 발견한 계약 누락이며 정식 `FAILURE_REPORT`가 아니다. 동일 Issue의 유효 실패 횟수는 0회다.

## 승인 기준과 작업공간

- Issue ID는 `R1-M5-03`, 보정 Work Order ID는 `R1-M5-03-C01`이다.
- 공식 작업공간은 `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`, Branch는 `codex/r1-m5-03`, 인계 HEAD는 `f11320d942cf5635d51fd9d0dbbce355f776f488`이다.
- `D:\Project\Daon_User`와 `C:\tmp`의 Clone·Worktree는 읽기 전용 보존 자료다. 수정·삭제·작업 전환을 금지한다.
- `AGENTS.md`, 상세 설계서, Release 1 구현계획·테스트계획, `R1-M5-03_work_order.md`와 이 작업지시서를 EOF까지 읽고 계약을 우선한다.
- 어울2가 이 범위의 유일한 코드 Writer다. 어울1은 결과 검토 전까지 같은 범위를 수정하지 않는다.

## 판단 이유

1. `local_objects`는 현재 Object ID·Workspace·Area·Key Version·Digest·Byte Size·Blob Name만 저장한다. 원 작업지시서가 요구한 검증 MIME, Object Version, Created/Updated, 상태가 없다.
2. `area_keys`는 Algorithm·생성/회전 시각·상태가 없고, File Header는 Magic·Key Version·Byte Size·Nonce·Digest만 가진다. 원 계약의 Header Version·Algorithm·Content Type Reference를 식별하거나 검증할 수 없다.
3. 경로는 사용 전 Symlink/Reparse/Hardlink를 검사하지만 검사 후 `open/read/replace` 사이 경로가 바뀌는 TOCTOU를 막는 Handle 기반 검증이 없다. Windows에서 최종 File/Directory Handle을 기준으로 Reparse 여부·정규 경계를 검증해야 한다.
4. Vector `embedding`이 유한수인지 확인하지 않아 `NaN`·`Infinity`가 sqlite-vec 직렬화·검색 경계에 진입할 수 있다.
5. Bootstrap 필수 Field가 추가됐는데 Protocol Version이 기존 `1.0`과 같아 구 Parent/Sidecar 조합을 명시적으로 구분하지 못한다.
6. Evidence 폴더에 결과 JSON은 있으나 표준 Manifest와 파일 Digest·검증 범위·Known Limit의 정본 연결이 없다.

## 조치 목표

- 기존 암호화 데이터와 설치형 검증을 깨지 않는 명시적 Schema Migration을 추가한다. 신규 설치뿐 아니라 현행 R1-M5-03 형식의 기존 저장소를 재개방하는 Upgrade Test를 둔다.
- `local_objects`에 검증된 Content Type Reference, Object Version, Created/Updated UTC, 상태를 저장·검증한다. API 입력은 허용 MIME 형식과 실제 Payload의 최소 Signature/Text 검증 결과가 일치해야 하며, 임의 문자열을 신뢰하지 않는다.
- `area_keys`에 Wrap Algorithm, 생성/회전 UTC, 상태를 기록한다. Key ID는 Workspace·Area·Version으로 안정적으로 식별한다.
- File 암호문 Header를 별도 Header Version과 Algorithm ID, Key Version, Nonce, 원문 Digest·Byte Size, Content Type Reference를 포함하는 형식으로 갱신한다. 모든 필드는 AEAD AAD 또는 Ciphertext 인증 범위에 포함하고, 알 수 없는 Version·Algorithm은 Fail-close한다.
- Windows 경로 접근은 Handle 기반으로 최종 Directory/File가 Storage Root 아래인지, Reparse Point가 아닌지, File Hardlink 수가 1인지 사용 시점에 검증한다. 검사와 사용을 분리한 단순 `Path` 재확인만으로 TOCTOU 완료로 판정하지 않는다.
- File 생성·교체는 동일 Directory의 안전한 Handle/원자 연산을 사용하고, DB Commit 실패·Process 중단 시 임시/고아 암호문 복구 정책과 Test를 둔다.
- Vector Put/Search 모두 모든 원소가 유한수이며 허용 Float32 범위인지 검증한다. `NaN`·양/음 `Infinity`·Overflow는 `LOCAL_VECTOR_INVALID`로 차단한다.
- 변경된 Bootstrap 계약에 맞춰 Parent와 Sidecar의 Protocol Version을 함께 올리고, 구 Version·필드 누락·혼합 조합을 Fail-close하는 Test를 추가한다.
- Python/Rust의 민감 Byte Buffer는 수명 종료·Lock·오류 경로에서 가능한 범위까지 Zeroize한다. JSON/String 복제로 완전 Zeroize할 수 없는 Runtime 한계는 숨기지 말고 Known Limit에 기록하며 Secret 값은 Log/Evidence에 남기지 않는다.

## 허용·제외 범위

- 허용: `services/local-service/`, `apps/desktop/src-tauri/`의 Storage·Bootstrap·Credential 관련 최소 코드와 Test, Dependency Lock/Packaging 수집 파일, R1-M5-03 진행기록·Evidence·완료보고.
- 기존 공개 Cloud API, Daon Connector, Studio UI, 다른 M5 Schema, 운영 DB/Compose를 변경하지 않는다.
- 기존 Storage Format을 조용히 폐기하거나 기존 암호문을 삭제·재생성하지 않는다. 안전한 Migration이 불가능하면 코드로 우회하지 말고 증거와 대안을 보고한다.
- 범위를 넓힌 리팩터링, 임시 절대주소, Secret 출력, Test 전용 우회 코드를 금지한다.

## TDD·필수 검증

- 먼저 각 누락을 재현하는 실패 Test를 추가한 뒤 최소 구현으로 통과시킨다.
- Schema/Header: 신규 저장, 구 R1-M5-03 저장소 Upgrade, 재시작, MIME 불일치, Header Version/Algorithm/Content Type 변조, Key 상태·Version 경계, 암호문/Tag/Digest 변조를 검증한다.
- 경로: Symlink·Junction/Reparse·Hardlink와 검사 직후 교체 Race를 실제 Windows Test로 검증한다. Race Test는 공격 경로가 Root 밖의 파일을 읽거나 덮어쓰지 못했음을 증명한다.
- Vector: 정상 Float32, Dimension/Metric/Version 경계와 함께 `NaN`·±`Infinity`·Float32 Overflow Put/Search를 모두 거부하는지 확인한다.
- Protocol: 새 Version 정상, 구 Version, 필드 누락, Parent/Sidecar 혼합을 검증한다.
- 기존 Python 전체, Ruff, strict Mypy, Rust Test, Clippy, JS Local Service Contract, Packaged Sidecar 2회 재시작, Independence와 공식 Quality Gate를 재실행한다.
- 실제 설치형 NSIS로 Credential 생성·재시작 동일 Key·암호문 재사용·Credential 철회 Fail-close·Uninstall을 다시 검증한다. GUI/Simulator를 사용하면 검증 직후 반드시 종료하고 Process·Listener·설치 Test Storage·Test Credential·Sidecar 잔여 0을 확인한다. 사용자의 기존 `%LOCALAPPDATA%\com.daon.user`는 변경하지 않는다.

## 진행·증거·결과 계약

- `docs/04_test_reports/release_1/R1-M5-03-C01_progress.md`에 착수, 계약 대조, 실패 Test, 단계별 구현, 오류·복구, 검증, 설치형 정리와 종료 직전을 시각·변경 파일·명령·결과·다음 작업과 함께 기록한다.
- 기존 `R1-M5-03_progress.md`에는 C01 판정과 현재 정본 위치·Branch·HEAD·다음 작업을 기존 이력을 삭제하지 않고 추가한다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-03-C01/`에 두고 `manifest.json`에 Work Order/Issue/Attempt/Baseline·Final SHA, 파일 SHA-256, Check별 범위·결과, Runtime Evidence, Known Limit와 자원 정리 결과를 기록한다.
- `docs/04_test_reports/release_1/R1-M5-03_completion_report.md`를 C01 최종 결과로 갱신한다.
- 결과보고는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`을 포함하고 `판정 → 판단 이유 → 조치` 순서로 반환한다.
- 완료 전 Local HEAD·Origin Branch·설치 Artifact SHA, Working Tree Clean, 잔여 Process/Listener/Test Storage/Test Credential 0과 정식 실패 횟수 0회를 보고한다.

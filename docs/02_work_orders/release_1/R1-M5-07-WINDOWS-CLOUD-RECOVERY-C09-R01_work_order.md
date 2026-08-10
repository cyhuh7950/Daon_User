# R1-M5-07 Windows Cloud Recovery Native Port C09-R01 수정 작업지시서

## 1. 문서 상태

- 상태: `READY`
- 원 issue_id: `R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-I001`
- 재작업: `R01` (`INCOMPLETE` 1회)
- 정본: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, branch `master`
- 기준 HEAD: `b9522084bcfd8235df541b886ea7a7d7c86fa7ec`
- 선행: C09 구현 결과 및 내부 독립 검토 `Critical 1 / Important 5`
- 진행 기록: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-R01_progress.md`
- 완료 보고: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-R01_completion_report.md`

## 2. 판정과 목적

- 판정: C09 기술 수락 불가, 동일 개발자 1차 재작업.
- 이유: 기존 66/66 회귀는 통과했으나 Safe Projection, 실제 ETag, 인증 회전, Secret 수명, Write 오류 의미와 실제 서버 계약 대표성에 중대 공백이 있다.
- 목적: C09 범위를 확장하지 않고 아래 6개 결함을 TDD로 보정한다.

## 3. 필수 정본

작업 전 `AGENTS.md`, 원 C09 작업지시서·progress·completion, 본 R01, 승인 설계·계획 Task 4, API Recovery 구현과 테스트, OpenAPI Recovery 계약, 현재 Rust 구현·계약 테스트를 EOF까지 읽고 Hash와 적용 조항을 R01 progress에 남긴다.

## 4. 허용 변경 범위

- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/native_session.rs` — Credential 비반환 내부 실행 경계 보정만
- `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- `apps/desktop/src-tauri/tests/native_session_contract.rs`
- `services/api/tests/test_recovery_runtime_http.py` — 계약 Fixture/회귀 보강만, 제품 API 변경 금지
- 원 C09 및 R01 progress/completion 문서

Cargo/Cargo.lock, Python API 제품, OpenAPI, Web/React, Local Service/Local Recovery, 사용자 삭제 31건과 기존 미추적 문서 3건은 수정하지 않는다.

## 5. 필수 보정 계약

### R01-1 Safe Projection 구체화

- 7개 작업별 응답을 `deny_unknown_fields` 구체 DTO로 역직렬화한다.
- 일반 `serde_json::Value`를 반환 DTO로 사용하지 않는다.
- Projection의 파생 Debug를 제거하거나 명시적 Safe Debug만 제공한다.
- nested unknown, password/token/authorization/header/url/body 변형이 Serialize·Debug에 도달하기 전에 거부되는 제품 경계 테스트를 추가한다.

### R01-2 ETag 상호운용

- 요청 종류별로 ETag를 검증한다.
- 목록 응답은 API 정본의 `\"projection-<digest>\"`, 개별 Backup/Restore는 실제 resource ETag 형식만 허용한다.
- 실제 API 형식의 목록·개별 ETag를 포함한 제품 경로 테스트를 추가한다.
- `If-Match`도 실제 Restore ETag 정본을 엄격 검증한다.

### R01-3 인증 만료와 권한 거부

- 인증 만료는 401로 한정한다. 403은 권한/Step-up Safe Error로 처리하고 Credential을 회전하지 않는다.
- GET 401: Refresh 최대 1회 후 요청 1회 재실행.
- Write 401: Refresh 1회만 수행하고 원 Write 호출 수는 1회로 유지한다.
- 각 경우의 Refresh/Transport 호출 수를 제품 Runtime 경계에서 검증한다.

### R01-4 Secret·소비 캐시 수명

- Idempotency Key와 Step-up 원문을 Port 수명 HashSet/일반 Value에 보존하지 않는다.
- 제한된 크기의 고정 길이 Digest 소비 캐시를 사용하고 원문은 ZeroizeOnDrop 비밀 타입으로 처리한다.
- 정상·검증 실패·Transport 실패·취소에서 원문 복사본 Zeroize와 캐시 상한을 검증한다.

### R01-5 Write 오류 의미

- Write Transport/응답 유실 오류는 `retryable:false`로 고정한다.
- GET의 안전한 Transport 오류만 `retryable:true`가 될 수 있도록 Method-aware 매핑한다.
- 같은 키 재사용 또는 새 키 자동 재시도를 유도하지 않는 테스트를 추가한다.

### R01-6 실제 계약 대표성

- 7개 계약 Fixture를 실제 Runtime DTO·ID·상태·시간·ETag·If-Match 형식과 일치시킨다.
- Actual reqwest 경로에서 timeout, slow-drip, malformed/missing/truncated/oversize Content-Length, 307·308 redirect destination hit 0을 검증한다.
- 합성 축약 Fixture의 PASS를 상호운용 PASS로 보고하지 않는다.

## 6. TDD·검증

각 결함은 기존 구현에서 실패하는 행동 RED를 먼저 기록하고 최소 GREEN으로 보정한다. 컴파일 오류는 행동 RED로 세지 않는다.

필수 검증:

1. C09/R01 표적 Rust 계약 전부
2. `node scripts/run-isolated-desktop-cargo.mjs test`
3. API Recovery 두 테스트 파일
4. Desktop lint, 소유 Rust rustfmt-check, `git diff --check`
5. Secret·금지 주소·허용 범위 Scan
6. `apps/desktop/src-tauri/gen`과 잔존 Cargo/Rustc 확인
7. 사용자 삭제 31건과 기존 미추적 문서 3건 보존

## 7. 완료 및 보고

- Commit·Push·배포·Browser·실제 Restore는 수행하지 않는다.
- `판정 → 판단 이유 → 조치` 순서로 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나를 제출한다.
- 변경 파일, 행동 RED/GREEN, 전체 회귀, 미해결 위험을 포함한다.

# R1-M5-07 Windows Cloud Recovery Native Port C09-R02 수정 작업지시서

## 1. 문서 상태

- 상태: `READY`
- 원 issue_id: `R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-I001`
- 재작업: `R02` (`INCOMPLETE` 누적 2회)
- 정본: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, branch `master`
- 기준 HEAD: `b9522084bcfd8235df541b886ea7a7d7c86fa7ec`
- 선행 판정: C09-R01 내부 재검토 `Critical 2 / Important 3`, 기술 수락 불가
- 진행 기록: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-R02_progress.md`
- 완료 보고: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-R02_completion_report.md`

## 2. 목적과 금지

R01에서 해소된 ETag 형식, 401/403 분리, GET 재실행·Write 무재실행, Method-aware retryable, Actual HTTP 오류 경계를 보존하면서 아래 5개 잔존 결함을 행동 TDD로 보정한다. 범위 확장, Cargo/Lock 변경, API 제품/OpenAPI/Web/Local Recovery 수정은 금지한다.

## 3. 필수 정본과 허용 파일

작업 전 `AGENTS.md`, 원 C09와 R01/R02 작업지시·progress·completion, 승인 설계·Plan Task4, API Runtime/Postgres/OpenAPI Recovery 계약, 현재 Rust 구현·테스트를 EOF까지 읽고 Hash·적용 조항을 R02 progress에 남긴다.

허용 변경:

- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/native_session.rs` — Credential 비반환 내부 경계 보정만
- `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- `apps/desktop/src-tauri/tests/native_session_contract.rs`
- API Recovery 테스트 — 계약 대표성 보강만, 제품 수정 금지
- C09/R01/R02 progress/completion

사용자 삭제 31건과 기존 미추적 문서 3건을 보존한다.

## 4. 필수 보정 계약

### R02-1 반환 문자열 Credential·Gateway 반사 차단

- 현재 Access/Bearer 및 고정 Gateway의 one-way canary를 응답 검증 경계에 전달한다.
- 역직렬화한 모든 반환 문자열과 Safe Error trace에서 exact, embedded, escaped Credential/Bearer/Gateway 반사를 Projection·Serialize·Debug 전에 거부한다.
- 최소 `workspace_id`, nested exclusion reason, `trace_id`에 Access·Bearer·Gateway를 주입한 제품 경계 RED/GREEN을 추가한다.
- Credential/Gateway 원문을 관찰자·로그·오류에 저장하지 않는다.

### R02-2 요청–응답 Resource 결속

비밀값이 없는 `CloudRequestContext`로 아래를 Projection 전에 검증한다.

- Get Backup: Path `backup_id` = 응답 `backup_id`
- Preview: Path `backup_id` = 응답 `backup_id`, 요청 Destination = 응답 Destination
- Get/Execute/Cancel Restore: Path `request_id` = 응답 `request_id`
- List/Create: 요청 Workspace = 모든 응답 `workspace_id`
- Resource ETag: 요청 대상 ID·응답 ID·Version 일치

다른 유효 자원·Workspace·Destination 응답과 일치하는 타 자원 ETag까지 함께 제공해도 거부되는 제품 테스트를 추가한다.

### R02-3 Wire Secret 취소 수명

- Step-up 등 민감 Body는 송신 Future가 소유하는 Zeroizing Stream/Buffer 경계로 전달하고 중간 일반 Vec/String 복사를 남기지 않는다.
- Idempotency/If-Match는 Credential이 아니므로 분류를 문서화하되, 불필요한 원문 장기 보존은 금지한다.
- 송신 중 정지된 Future를 실제 drop/abort하고 정상·timeout·early error에서 앱 소유 민감 Buffer Drop/Zeroize를 검증한다.
- 외부 reqwest 내부 메모리까지 보장할 수 없는 부분은 보장 범위를 정확히 보고하고 PASS를 과장하지 않는다.

### R02-4 실제 DTO·상태·Status

- Backup 상태와 transition에 실제 `expired`를 포함한다.
- `manifest_digest`는 non-null 필수로 고정한다.
- Restore Destination은 `fixture-` 계약을 엄격히 적용한다.
- Create Backup·Preview는 정확히 201, 나머지 성공은 정확히 200만 허용한다.
- 각 잘못된 상태/null digest/destination/status를 부정 테스트로 고정한다.

### R02-5 Idempotency와 Cache 수명

- Idempotency-Key는 OpenAPI/서버 안전 교집합인 16–128자와 서버 safe-ID 문자 집합으로 제한한다.
- 모든 성공 Fixture도 실제 유효 Key로 교체한다.
- Digest 소비 캐시는 고정 상한을 유지하되 128회 이후 영구 Write lockout을 만들지 않는 명시적 만료·세대교체 또는 제한 LRU 정책을 적용하고 테스트한다.
- 계약 테스트 helper가 상수 반환으로 검증을 위조하지 않도록 실제 digest-only 저장 구조와 상한/회수 동작을 관찰한다.

## 5. TDD·검증

각 항목은 기존 구현에서 실패하는 행동 RED를 먼저 기록하고 최소 GREEN으로 보정한다. 컴파일 오류·도구 timeout은 행동 RED나 실패 횟수로 세지 않는다.

필수 검증:

1. R02 신규 Critical/Important 계약과 전체 Recovery Bridge
2. Native Session 계약
3. `node scripts/run-isolated-desktop-cargo.mjs test`
4. API Recovery 두 테스트
5. Desktop lint, 소유 Rust rustfmt-check, `git diff --check`
6. Secret·금지 주소·허용 범위·Cargo/Lock/API/Web/Local Service 무변경 Scan
7. `gen`, Cargo/Rustc 잔존과 사용자 Dirty 보존 확인

## 6. 완료·보고

- Commit·Push·배포·Browser·실제 Restore는 수행하지 않는다.
- `판정 → 판단 이유 → 조치` 순서로 결과를 제출한다.
- 동일 issue의 세 번째 `INCOMPLETE` 또는 정식 `FAILURE_REPORT`가 되면 즉시 쓰기를 중단하고 어울1에게 반환한다.

## 7. 어울1 직접 구현 인수 승인

- 2026-08-11: R02 독립 검토에서 실제 송신 Body가 `wire.bytes.to_vec()` 일반 복사본을 소유하는 Important 1건이 남아 동일 issue의 `INCOMPLETE`가 합계 3회에 도달했다.
- 어울2 쓰기를 중지하고 신산님에게 보고했으며, 신산님이 어울1의 `DIRECT_IMPLEMENTATION`을 승인했다.
- 근본 원인은 Daon 호출부의 일반 `Vec` 복사다. `reqwest` 내부 복사로 오분류하지 않는다.
- 직접 구현에 한해 `apps/desktop/src-tauri/Cargo.toml`과 `Cargo.lock`을 허용한다. 현재 Lock에 이미 존재하는 `bytes = 1.12.1`만 직접 Pin하여 `Bytes::from_owner`로 Zeroizing 소유자를 실제 송신 Body에 이전한다. 다른 의존성·버전·기능은 변경하지 않는다.
- 실제 송신 Owner의 정상·오류·Timeout·Future Abort Drop과 exact·Unicode escaped 반사 거부 테스트만 보강하고 기존 R02 계약을 보존한다.

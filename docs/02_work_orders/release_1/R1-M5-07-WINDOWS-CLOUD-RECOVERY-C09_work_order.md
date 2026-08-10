# R1-M5-07 Windows Cloud Recovery Native Port C09 작업지시서

## 1. 문서 상태

- 상태: `READY`
- issue_id: `R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-I001`
- 정본: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, branch `master`
- 기준 HEAD: `93044d116c284db5a736b4195d9d1f993fe75c0f`
- 선행: Native Session C07 및 Local Recovery C08 기술 수락·origin/master 통합 완료
- 진행 기록: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09_progress.md`
- 완료 보고: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09_completion_report.md`

## 2. 목적

Windows 설치형 Rust 계층에 Cloud Recovery 7개 계약만 허용하는 Native Port를 구현한다. Vault Access는 Rust 내부 Authorization에만 사용하며 JavaScript·Safe DTO·Debug·Error·Log·Evidence로 반환하지 않는다. 기존 Web Recovery, Native Identity 두 경로, Local Recovery, CSP와 공개 API 계약은 보존한다.

## 3. 필수 정본

작업 전 다음을 EOF까지 읽고 SHA-256과 적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- 승인 상세 설계서, Release 1 구현계획 1.8, 테스트계획 0.9
- `docs/superpowers/plans/2026-08-10-windows-recovery-native-bridge.md` Task 4
- Windows Recovery 설계 1.1 및 승인 기록
- Native Session C04~C07와 Local Recovery C08 최종 progress/completion
- `apps/desktop/src-tauri/src/native_session.rs`
- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `services/api/tests/test_recovery_runtime_http.py`
- `services/api/tests/test_recovery_contract.py`
- OpenAPI Recovery 7개 Path 계약

## 4. 허용 변경 범위

- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/native_session.rs` — Vault Credential을 반환하지 않는 Rust 내부 실행 경계만 허용
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- `apps/desktop/src-tauri/tests/native_session_contract.rs`
- `services/api/tests/test_recovery_runtime_http.py` — 서버 계약 회귀 보강만 허용, 제품 API 수정 금지
- C09 progress/completion 문서

Cargo/Cargo.lock, Python API 제품, OpenAPI, Web/React, Local Service, Local Recovery 계약, 사용자 삭제 31건과 기존 미추적 문서 3건은 수정하지 않는다. 계획과 실제 코드가 충돌하면 증거와 함께 어울1에게 되돌리고 범위를 임의 확장하지 않는다.

## 5. 구현 계약

### C09-1 고정 Cloud Allowlist

다음 7개 Method/Path만 허용한다.

1. `POST /api/v1/backups`
2. `GET /api/v1/backups`
3. `GET /api/v1/backups/{id}`
4. `POST /api/v1/backups/{id}/restore-previews`
5. `GET /api/v1/restore-requests/{id}`
6. `POST /api/v1/restore-requests/{id}/execute`
7. `POST /api/v1/restore-requests/{id}/cancel`

ID·Query·Body·응답 크기·Content-Type·Timeout을 Fail-closed 검증한다. 임의 URL, Redirect, Cookie, localhost/127.0.0.1, Docker Host, 환경변수 Gateway와 그 밖의 Method/Path를 거부한다.

### C09-2 Credential 비노출

- Access Credential은 Windows Vault에서 읽어 Rust HTTP Authorization Bearer에만 넣는다.
- Credential 원문을 반환하는 Public 함수, Tauri Command, Safe DTO를 추가하지 않는다.
- Request/Response/Debug/Error/Log에 Authorization, Token, Public Gateway 전체 URL을 포함하지 않는다.
- Secret Request/Response Buffer는 Drop·취소·오류에서 Zeroize한다.
- 인증 부재·만료·Vault 오류는 `AUTHENTICATION_REQUIRED`로 Fail-closed한다.

### C09-3 상태변경 계약

- Preview와 Execute는 서로 다른 Step-up 값을 요구한다.
- Execute·Cancel은 `If-Match`, 모든 Write는 `Idempotency-Key`가 필수다.
- 누락, 재사용, ETag 충돌과 서버 4xx/5xx는 상태변경 요청을 자동 재실행하지 않는다.
- Access 만료 시 Session 계층은 Refresh를 최대 1회 수행할 수 있으나 원 상태변경 요청을 자동 재실행하지 않는다.
- GET만 새 Credential로 1회 안전 재시도를 허용할 수 있으며 계약 테스트로 고정한다.

### C09-4 응답과 오류 정본

- 서버의 Safe Recovery DTO만 Rust Safe Projection으로 변환한다.
- Unknown field, 잘못된 ID/상태/ETag/시간, Oversize, Redirect, Non-JSON, Chunked/Truncated 응답을 거부한다.
- 오류는 안정된 `{code, trace_id, retryable}` Safe Projection으로 반환하고 Header·Token·URL·Body 원문을 포함하지 않는다.

## 6. TDD·검증

각 계약은 기존 구현에서 실패하는 RED를 먼저 기록한 후 최소 GREEN으로 구현한다.

필수 검증:

1. Cloud Recovery Allowlist·Header·Step-up·If-Match·Idempotency·무재실행 Mock/Actual Transport 계약
2. `node scripts/run-isolated-desktop-cargo.mjs test`
3. `uv run --isolated --project services/api --frozen python -m pytest services/api/tests/test_recovery_runtime_http.py services/api/tests/test_recovery_contract.py -q`
4. Desktop lint 및 소유 Rust rustfmt-check
5. `git diff --check`
6. Secret·내부 URL·금지 주소 Scan, 허용 범위와 Dirty 보존 확인

환경·도구 중단은 정식 실패로 세지 않는다. 장시간 Cargo는 한 번만 실행하고 중복 프로세스를 시작하지 않는다.

## 7. 완료 조건

- Cloud 7개만 실제 Rust 계약으로 허용된다.
- Vault Credential이 Rust 내부 Authorization 밖으로 노출되지 않는다.
- Step-up·If-Match·Idempotency와 상태변경 무재실행이 검증된다.
- 기존 Native Session 19건, Local Recovery 16건, Local Service 5건과 API Recovery 계약이 회귀 통과한다.
- 사용자 삭제 31건과 기존 미추적 문서 3건을 보존한다.
- Commit·Push·배포·Browser·실제 Restore 없이 어울1에게 결과를 제출한다.

## 8. 결과보고

`판정 → 판단 이유 → 조치` 순서로 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나를 제출한다. 변경 파일, RED/GREEN, 전체 회귀, 미해결 위험과 다음 판단을 포함한다.

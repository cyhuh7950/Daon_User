# R1-M5-07 Windows Local Recovery Native Port R01 완료보고

## 판정

`COMPLETED` — issue `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-I001`의 R01 보안·정본·운영 보정을 완료했고 지정 회귀를 통과했다.

## 판단 이유

| 요구사항 | 구현/검증 근거 |
| --- | --- |
| R01-1 Native Context 비노출 | `AppCredentials::Debug` storage root redaction; `each_app_launch_generates_distinct_credentials`; `malformed_timestamp_version_chain_and_native_canaries_are_rejected`가 실행 시 생성한 Port·Root Secret·Token·Storage Root·격리 경로를 응답 문자열 필드별로 거부 |
| R01-2 Job 정본 | `canonical_failed_job_and_version_chain_are_accepted`; malformed timestamp 및 v1/null·vN/N-1 위반 거부 |
| R01-3 Parser 오류 보존 | `parser_rejection_is_non_retryable_and_safe_error_has_opaque_trace`; `connectivity_failure_remains_retryable_and_distinct_from_parser_rejection`; 실제 TCP forged/truncated/oversize/header-limit 거부 |
| R01-4 Deadline/Lifecycle | Manager lock 안에서 ready/running·Port·요청별 Token snapshot까지만 수행하고 TCP 전 해제; connect/write/read에 단일 남은 deadline 적용; `slow_drip_obeys_overall_deadline_without_blocking_status_or_shutdown` |
| R01-5 실제 제품 경로 | `manager_real_loopback_path_binds_method_path_command_and_unique_tokens`; Manager→Loopback TCP→Parser→Port 경로, Method/Path/Command Binding 및 요청별 고유 Token 검증; Python `test_recovery.py`와 `test_app.py` 24 PASS |
| R01-6 Safe Error | `{code, trace_id, retryable}` 직렬화, 32자리 소문자 hex trace, Debug/DTO Credential·Port·Root·경로 비노출 검증 |

원 코드의 RED는 storage root Debug 노출, failed 거부, malformed timestamp 허용, Parser 오류 축약으로 실제 확인했다. 실제 Manager/TCP 계약은 FakeTransport-only 경계를 제거하는 최소 snapshot/deadline 구현과 함께 추가했으며, 표적 16/16 및 최종 제품 회귀로 GREEN을 고정했다.

## 생성·변경 결과

- R01 제품 보정: `apps/desktop/src-tauri/src/local_service.rs`, `apps/desktop/src-tauri/src/recovery_bridge.rs`.
- R01 계약 보강: `apps/desktop/src-tauri/tests/local_service_contract.rs`, `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`.
- 기록: 이 완료보고와 `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-R01_progress.md`.
- 원 C08의 `lib.rs`, Cargo wrapper, 원 progress/completion은 보존했다. Local Service Python 제품·테스트는 수정하지 않았다.

## 테스트 결과

- `node scripts/run-isolated-desktop-cargo.mjs test`: 52/52 PASS, exit 0.
- Recovery 표적(동적 Canary 최종 보강 후): 11/11 PASS.
- `uv run --isolated --project services/local-service --frozen python -m pytest services/local-service/tests/test_recovery.py services/local-service/tests/test_app.py -q`: 24/24 PASS.
- `node --test scripts/tests/desktop-local-service.test.mjs`: 10/10 PASS.
- `npm run verify:desktop-lint`: PASS.
- `git diff --check`: PASS(LF/CRLF 안내만 존재).

## 미해결 사항/조치

- 자동·정적·실제 Loopback 검증 범위에는 미해결 결함이 없다.
- Browser, 실제 설치, 운영 Credential/데이터 Restore, 배포는 금지 범위라 수행하지 않았다.
- 사용자 삭제 31건과 원래 미추적 문서 3건은 그대로 보존했다.
- 어울1이 완료보고와 변경 diff를 독립 검토해 최종 수락 여부를 판단해야 한다.

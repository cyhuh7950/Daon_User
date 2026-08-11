# R1-M5-07 Windows Recovery 권한 Projection C10B-R01 완료 보고서

## 판정

- 상태: `COMPLETED`
- Work Order: `R1-M5-07-WINDOWS-AUTHZ-C10B-R01`
- Issue ID: `R1-M5-07-WINDOWS-AUTHZ-C10B-I001`
- 기준선: `master` · `bc4ea833a897086ead6da29cd21f5bce6cb79085`

## 판단 이유

- Rust 권한 Projection은 빈 목록 또는 exact 정렬 7종 전체만 허용하며 1~6개 부분집합을 Fail-close한다.
- 실제 `GET /api/v1/session` 응답은 HTTP 200만 성공으로 인정하고 같은 Safe Body의 201·202·206을 거부한다.
- 성공 뒤 전송 오류가 발생해도 이전 성공 Projection을 재사용하지 않고 Vault 상태를 변경하지 않는다.
- `/api/v1/session` OpenAPI가 `ServiceUnavailable` 503을 명시하며 Verifier가 누락을 거부한다.
- API는 `ACTION_DENIED`만 인증된 빈 목록으로 처리하고 감사·저장소 unavailable 오류는 HTTP 503·retryable Safe Error로 보존한다.

## 변경 결과

- Rust·계약 Test: `apps/desktop/src-tauri/src/native_session.rs`, `apps/desktop/src-tauri/tests/native_session_contract.rs`
- API Test: `services/api/tests/test_runtime_http.py`
- OpenAPI·Verifier·Test: `packages/contracts/openapi/v1/openapi.json`, `scripts/verify-openapi-contract.mjs`, `scripts/tests/openapi-contract.test.mjs`
- 파생 Summary: `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`
- R01 기록: 본 보고서와 `R1-M5-07-WINDOWS-AUTHZ-C10B-R01_progress.md`

## 테스트 결과

| 구분 | 결과 |
|---|---|
| API Runtime·Identity | `28/28 PASS` · 오류 보존 subtest 2 PASS |
| OpenAPI·Tauri Node | `31/31 PASS` |
| OpenAPI Summary | write 후 no-write `PASS`; SHA-256 `F52FFC92998DA4D5161D683FC32506C7062207F300E8C1C8D865AA2417ECA106` |
| Desktop lint | `PASS` · 4 files |
| Rust isolated Cargo | `89/89 PASS` (`18 + 5 + 22 + 44`) |
| Format·Syntax·Diff | Rust rustfmt, Node syntax, `git diff --check` 모두 `PASS` |
| 잔존 자원 | Tauri `gen` 0, Cargo/Rustc Process 0 |

## 보존·미해결 사항

- 기존 C10B/C10 미커밋 변경, 사용자 추적 삭제 31건, 원 미추적 문서 3건을 복원·삭제·Stage하지 않았다.
- Cargo/Lock·DB Migration·Vault Schema·React/UI는 변경하지 않았다.
- 기존 `.venv` pytest 손상 때문에 제품 변경 없는 격리 uv 환경에서 동일 API 테스트 범위를 실행했다.
- Commit·Push·배포·Browser·실제 Login/Recovery는 수행하지 않았다.

## 조치

- 어울1이 R01 Diff와 증거를 독립 검토한다.
- 합격 시 C10-R01에서 React Adapter가 본 Safe Projection Command를 연결한다.

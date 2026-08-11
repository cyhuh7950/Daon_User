# R1-M5-07 Windows Recovery 권한 Projection C10B 완료 보고서

## 판정

- 상태: `COMPLETED`
- Work Order: `R1-M5-07-WINDOWS-AUTHZ-C10B`
- Issue ID: `R1-M5-07-WINDOWS-AUTHZ-C10B-I001`
- 기준선: `master` · `bc4ea833a897086ead6da29cd21f5bce6cb79085`

## 판단 이유

- `GET /api/v1/session`이 현재 Principal·Primary Workspace의 기존 `Action.POLICY_MANAGE` 판정을 Cloud Recovery 7종의 정렬·중복 없는 최소 Operation 목록으로만 투영한다.
- `ACTION_DENIED`는 인증 상태를 유지한 빈 목록으로 닫고, 인증·저장소·감사 오류는 권한 거부로 축약하지 않는다.
- Rust 입력 0개 Command `native_recovery_authorization_status`가 Vault Access를 JavaScript에 반환하지 않고 고정 Public Gateway의 Session Endpoint를 읽기 전용으로 조회한다.
- Rust 응답은 Safe Session 전체 일치와 strict allowlist를 검증하며 Unknown field·Operation, 중복·비정렬, Session/Workspace 불일치, 전송 오류에서 이전 성공 결과를 재사용하지 않는다.
- Projection은 기존 `DaonUser/NativeSession/v1` Vault JSON과 다른 지속 저장소에 추가하지 않았다.

## 변경 결과

- API·Test: `services/api/src/daon_user_api/runtime.py`, `services/api/tests/test_runtime_http.py`
- OpenAPI·Verifier: `packages/contracts/openapi/v1/openapi.json`, `scripts/tests/openapi-contract.test.mjs`, `scripts/verify-openapi-contract.mjs`
- 파생 증거: `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`
- Rust·Tauri·Contract Test: `apps/desktop/src-tauri/src/native_session.rs`, `apps/desktop/src-tauri/src/lib.rs`, `apps/desktop/src-tauri/tests/native_session_contract.rs`, `scripts/tests/desktop-tauri-shell.test.mjs`
- 진행·완료 기록: 본 보고서와 `R1-M5-07-WINDOWS-AUTHZ-C10B_progress.md`

## 테스트 결과

| 구분 | 결과 |
|---|---|
| API Runtime·Identity | `28/28 PASS` (`uv run --isolated --with pytest`) |
| OpenAPI·Tauri Node 계약 | `30/30 PASS` |
| OpenAPI 파생 Summary | `--write` 갱신 후 no-write `PASS`; SHA-256 `7025EEAD41E57BD40D0B0D9D15578B7191A4B688EF14F60F76EBC9F92C54E44D` |
| Desktop lint | `PASS` · 4 files |
| Rust isolated Cargo | `88/88 PASS` (`18 + 5 + 21 + 44`) |
| Rust format | 허용 3파일 `rustfmt --check` `PASS` |
| Diff/잔존 자원 | `git diff --check` `PASS`; `gen` 0; Cargo/Rustc Process 0 |

## 보존·미해결 사항

- 기존 C10 React Adapter 미커밋 변경, 사용자 추적 삭제 31건, 원 미추적 문서 3건을 복원·삭제·Stage하지 않았다.
- Cargo/Lock·DB Migration·Recovery Port·React/UI/CSS/CSP/환경 설정은 변경하지 않았다.
- 기존 `.venv`의 pytest가 `ModuleNotFoundError: _pytest._code`로 손상되어 작업지시 원문 API 명령은 실행할 수 없었다. 제품 변경 없는 격리 uv 환경에서 동일 테스트 범위를 fresh 실행해 28/28 PASS를 확보했다.
- Commit·Push·배포·Browser·실제 Login/Recovery는 수행하지 않았다.

## 조치

- 어울1이 허용 Diff와 테스트 증거를 독립 검토한다.
- 승인 시 C10-R01에서 기존 React Adapter가 `native_recovery_authorization_status`의 Safe Operation 목록을 사용하도록 연결한다.

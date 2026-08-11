# R1-M5-07 Windows Recovery 권한 Projection C10B 진행 기록

## 2026-08-11 · S0 착수·승인 기준선 확인 · COMPLETED

- 시각: 2026-08-11 KST
- 단계·상태: S0 착수·승인 기준선 확인 · COMPLETED
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`
- Git 기준선: `master` · `origin=git@github-cyhuh7950:cyhuh7950/Daon_User.git` · HEAD `bc4ea833a897086ead6da29cd21f5bce6cb79085`
- 승인·적용 문서: `AGENTS.md`, 상세 설계 0.9, Release 1 구현계획, Windows Recovery 설계 1.3 §5.3.2·§5.5·§7·§9, Native Bridge Plan Task 4.6·Global Constraints·Completion Contract, C10 완료·독립 검토 근거, C10B 작업지시서·프롬프트를 EOF까지 확인했다.
- SHA-256: AGENTS `AABB11177EA7541B62C0AD6E6AB2FD745FCD4ADED72A25DF98522FC8E41B47EA`; 상세 설계 `6FF5E944C4C7BA66A73B82333A9172391B7ED96F2B532FABB7779BC28518F418`; 구현계획 `58B677D24D356499CB1891E4B5C5366657F9BC7A774E68E335016809A1CF8F31`; Windows Recovery 설계 `86634AD2A3A58035099D2BEDCD44DC8203C9122B381A15E0DDB3BBFC6FE8307A`; Native Bridge Plan `BDCA37ED9BA12F1B9DA0F4F7482B3C3DD71E2CE49094E1D5CD1D13F6F1FEC6EA`; C10B WO `F5AABE1FFF69FC34CA99B92BAEC358163A8DBB71A74138AB49E1A8F8AF646903`; Prompt `11930EDD4E01B47C10CD200D3FEF18D20E08EC60BE9EF9BFE5809BDCDC914B20`.
- 보존 상태: 기존 C10 React Adapter 미커밋 변경, 사용자 추적 삭제 31건, 원 미추적 문서 3건을 확인했으며 C10B 허용 경로 밖 파일은 수정·복원·Stage하지 않는다.
- 변경 파일: 본 Progress 신규 1건.
- 명령·검증: `git rev-parse --show-toplevel`, `git branch --show-current`, `git remote get-url origin`, `git rev-parse HEAD`, `git status --short`, 문서 SHA-256·EOF 확인.
- 오류·원인·복구: 없음.
- 다음 작업: 현재 API Session·AuthorizationService·OpenAPI·Rust Native Session 계약을 조사하고, 실제 동작을 검증하는 API RED 테스트부터 작성한다.
- 금지 작업: Commit·Push·배포·Browser·실제 Login/Recovery 0건.

## 2026-08-11 · S1 API Projection RED · RED_CONFIRMED

- 시각: 2026-08-11 KST
- 단계·상태: S1 API Projection RED · RED_CONFIRMED
- 변경 파일: `services/api/tests/test_runtime_http.py`, 본 Progress.
- 선작성 계약: 현재 Workspace만 사용하고 헤더 위조를 무시하는 7종 정렬 Projection, `ACTION_DENIED`의 인증 유지·빈 목록, 감사/저장소 오류를 권한 거부로 축약하지 않는 오류 보존, 역할·Permission·Credential 비노출.
- RED 명령: `uv run --isolated --with pytest --project services/api python -m pytest services/api/tests/test_runtime_http.py -q`.
- RED 결과: 기존 20 PASS, 신규 3 FAIL. 세 실패 모두 기존 Session 응답에 `recovery_operations`가 없고 AuthorizationService가 호출되지 않아 발생했으므로 승인 기능 부재를 정확히 재현했다.
- 오류·원인·복구: 작업지시 원문 명령은 기존 `.venv`의 손상된 pytest(`ModuleNotFoundError: _pytest._code`)로 제품 실행 전 중단했고, 첫 격리 실행은 pytest 미포함으로 중단됐다. 저장소를 변경하지 않는 `uv run --isolated --with pytest`로 복구해 유효 RED를 확보했다. 두 환경 중단은 제품 실패가 아니다.
- 다음 작업: `GET /api/v1/session`에 기존 AuthorizationService의 `Action.POLICY_MANAGE` 평가를 최소 연결하고 `ACTION_DENIED`만 빈 목록으로 처리한다.

## 2026-08-11 · S2 API·OpenAPI GREEN · COMPLETED

- 단계·상태: S2 API·OpenAPI GREEN · COMPLETED
- 변경 파일: `services/api/src/daon_user_api/runtime.py`, `services/api/tests/test_runtime_http.py`, `packages/contracts/openapi/v1/openapi.json`, `scripts/tests/openapi-contract.test.mjs`, `scripts/verify-openapi-contract.mjs`, 승인 추가 경로 `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`.
- 구현: 현재 Principal·Primary Workspace에 기존 `Action.POLICY_MANAGE`를 평가하고, 허용 시 정렬된 Cloud 7종 전체, `ACTION_DENIED` 시 빈 배열만 Session Safe Projection에 추가했다. 다른 Authorization 오류는 기존 Middleware로 전달한다.
- API 검증: 격리 uv로 `test_runtime_http.py`와 `test_identity_local_auth.py` 합계 28/28 PASS.
- OpenAPI RED/GREEN: 스키마 부재·Unknown/중복 Operation 허용을 13 PASS/2 FAIL로 재현한 뒤 `workspace_id`와 exact enum·`uniqueItems` 계약 및 검증기 Mutation 차단을 추가해 15/15 PASS.
- 결정적 증거: 어울1이 C10B 허용 경로에 OpenAPI Summary 한 파일을 추가 승인했다. `--write` 후 no-write 검증 PASS, canonical SHA-256 `7025EEAD41E57BD40D0B0D9D15578B7191A4B688EF14F60F76EBC9F92C54E44D`.
- 오류·복구: 첫 JSON Patch가 유사 Native Credential Session 블록에 일부 적용되어 Node 계약이 즉시 실패했다. Diff로 위치를 확인하고 Native Credential 계약을 원상 보존한 뒤 Identity Session에만 최소 교정했으며 15/15 PASS로 재확인했다.
- 다음 작업: Rust 실제 GET/strict Projection/Vault 비저장/Tauri Command RED→GREEN.

## 2026-08-11 · S3 Rust Projection·Tauri Command RED→GREEN · COMPLETED

- 단계·상태: S3 Rust Projection·Tauri Command RED→GREEN · COMPLETED
- 변경 파일: `apps/desktop/src-tauri/src/native_session.rs`, `apps/desktop/src-tauri/src/lib.rs`, `apps/desktop/src-tauri/tests/native_session_contract.rs`, `scripts/tests/desktop-tauri-shell.test.mjs`.
- RED: Node Command 계약 14 PASS/1 FAIL. 첫 Cargo는 새 Projection·fetch API 부재 E0432/E0599, 두 번째 Cargo는 no-cache Test constructor·Debug 부재 E0599/E0277로 각각 승인 기능 부재를 재현했다.
- 구현: 고정 `GET /api/v1/session`, Rust 내부 Bearer, Redirect/Cookie/비JSON/크기 제한, `deny_unknown_fields` Envelope·Meta·Data, exact 정렬·중복 없는 Operation allowlist, 현재 Vault Session 전체 일치, Projection 비저장, 과거 성공 재사용 없는 입력 0개 `native_recovery_authorization_status`를 최소 구현했다.
- GREEN: 격리 전체 Cargo `18 + 5 + 21 + 44 = 88/88 PASS`; Native 계약은 신규 실제 TCP GET·악성 응답 5종과 성공 후 오류 재사용 금지·Vault 비저장을 포함해 21/21 PASS.
- 오류·복구: 첫 Cargo RED는 124초 timeout 뒤 생성물 자동 정리 중 재실행이 일시 거부됐으나 gen 부재를 확인하고 장시간 재실행해 유효 RED를 회수했다. `cargo fmt`가 Package의 허용 밖 기존 Rust 3파일을 포맷해 착수 clean 상태와 대조 후 해당 3파일만 exact `git restore --worktree`로 복원했다.
- 다음 작업: 필수 fresh 회귀·보안 Scan·허용 범위·Dirty·잔존 자원 검증 후 Completion Report 작성.

## 2026-08-11 · S4 최종 검증·종료 · COMPLETED

- 시각: 2026-08-11 KST
- 단계·상태: S4 최종 검증·종료 · COMPLETED
- Fresh 검증: API `28/28 PASS`; OpenAPI·Tauri Node 계약 `30/30 PASS`; OpenAPI 파생 Summary write 후 no-write `PASS`; Desktop lint `PASS`; Rust 전체 `88/88 PASS`; 허용 Rust `rustfmt --check` `PASS`; `git diff --check` `PASS`.
- 보안·경계 확인: Browser/JavaScript 공개 값은 `recovery_operations`뿐이며, Bearer는 Rust 내부 고정 Public Gateway의 `GET /api/v1/session`에만 사용한다. 응답은 Unknown field·Unknown/중복/비정렬 Operation·Session/Workspace 불일치·Cookie·비JSON·Redirect·크기 초과에서 Fail-close하고 Projection은 Vault JSON에 저장하지 않는다.
- 범위 확인: Cargo/Lock·DB Migration 변경 0건, `gen` 경로 0건, Cargo/Rustc 잔존 Process 0건. 기존 C10 미커밋 변경, 사용자 추적 삭제 31건, 원 미추적 문서 3건을 보존했다.
- 환경 참고: 작업지시 원문 API 명령은 기존 `.venv`의 손상된 pytest로 실행 불가했으며, 저장소를 변경하지 않는 `uv run --isolated --with pytest` 동등 범위로 fresh 28/28 PASS를 확보했다.
- 금지 작업: Commit·Push·배포·Browser·실제 Login/Recovery 0건.
- 다음 작업: 어울1의 독립 Diff·증거 검토 후 C10-R01에서 React Adapter와 본 Command를 연결한다.

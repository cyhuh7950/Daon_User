# R1 사용자 제품 분리 Stage C 완료 보고서

## 판정

`COMPLETED / APPROVED`

## 수행 결과

- Windows 사용자 프로그램에 Product Workspace 전용 Tauri Command 7개를 등록했다.
- Native Vault의 Access·Refresh는 `native_session.rs` 내부에서만 사용하고 WebView·응답·Debug·Error로 반환하지 않는다.
- Workspace 요청은 고정 Gateway와 고정 Method·Path만 사용한다. 임의 URL·Method·Path와 Credential getter는 추가하지 않았다.
- Session 없음과 Workspace 불일치는 Transport 호출 전 `AUTHENTICATION_REQUIRED`로 닫는다.
- 입력은 Command별 `deny_unknown_fields` DTO로 제한하고 PDF filename·MIME·signature·25MiB, 질문·Studio 필드를 검증한다.
- JSON 응답은 128KiB, Citation PDF는 25MiB로 분리하고 status·Content-Type·Content-Length·unknown field·Workspace meta·lineage를 검증한다.
- Redirect를 따르지 않고 connect 5초·전체 20초 timeout을 적용하며 oversize·truncated·malformed 응답과 Access·Refresh·Gateway 반사를 거부한다.
- Write는 자동 재실행하지 않으며 공용 Shell의 logical-request fingerprint를 Rust가 고정 16~128 Safe Idempotency Key로 변환한다. 같은 fingerprint·payload는 같은 Key, 변경된 요청은 다른 Key다.
- Desktop Session 수명당 Windows Adapter 한 개를 주입하고 Session ID를 React `key`로 사용해 Workspace Tree를 재마운트한다. 질문·보고서·Citation은 Session lifetime AbortSignal을 공유해 logout/session change 뒤 이전 결과를 반영하지 않는다.
- Windows Citation 클릭은 선택적 `citationContent` Command로 PDF bytes를 받은 뒤 서버 `X-Citation-Page`와 요청 page를 결속 검증하고 `blob:` URL을 연다. 60초 만료뿐 아니라 Session cleanup에서도 즉시 revoke한다. Web Adapter의 기존 same-origin Citation URL은 유지한다.
- Rust 응답 Projection은 OpenAPI nullable `job_state`, Upload `object_id` 32 lowercase hex·digest 64 lowercase hex·입력 byte size·state를 검증한다. Studio Report는 HTTP `200 + replayed=true`, `201 + replayed=false`만 허용한다.
- PDF filename은 Runtime과 동일하게 C0 제어문자·슬래시를 거부하고, 질문·제목·목적 등 길이는 UTF-8 byte 수가 아닌 문자 수로 검증해 정상 한국어 입력을 보존한다.

## 변경 파일

- `apps/desktop/src-tauri/src/workspace_bridge.rs` (신규)
- `apps/desktop/src-tauri/src/native_session.rs`
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/tests/workspace_bridge_contract.rs` (신규)
- `apps/desktop/src/windows-workspace-adapter.js` (신규)
- `apps/desktop/src/desktop-shell.jsx`
- `packages/ui/src/product-workspace-shell.jsx`
- `scripts/run-isolated-desktop-cargo.mjs`
- `scripts/tests/windows-workspace-adapter.test.mjs` (신규)
- `scripts/tests/desktop-tauri-shell.test.mjs`
- `scripts/tests/product-workspace.test.mjs`
- 본 Progress·Completion 문서

## TDD·검증 근거

- Node RED: Windows Adapter module 부재 `ERR_MODULE_NOT_FOUND`.
- Rust RED: `workspace_bridge` module 부재. 첫 재현은 Tauri permission manifest 환경 차단과 분리 기록했다.
- Node 최종: `32 passed, 0 failed`.
- Rust 최종: `103 passed, 0 failed`.
  - lib 30(Workspace fixed path, Session/Workspace network0, strict projection, 실제 write header/body/status, Citation page, nullable·Unicode·Idempotency 포함)
  - Local Service 5
  - Native Session 22
  - Recovery Bridge 44
  - Workspace integration contract 2
- `npm run verify:desktop-lint`: PASS.
- `npm run verify:desktop-build`: PASS, Vite 26 modules.
- `npm run verify:product-ui-boundary`: PASS, 233 files, violation 0, boundary error 0.
- `node --check`, 수정 Rust `rustfmt`, `git diff --check`: PASS.
- Tauri `gen` 잔존 0, Cargo/Rustc 잔존 Process 0.

## 기존 상태 보존

- 사용자 기존 삭제 27건을 복원·수정·Stage하지 않았다.
- `Cargo.toml` Worktree Blob과 HEAD Blob은 `bbf68886...d855`로 동일하다.
- 기존 Native Evidence·완료/진행 보고와 기존 미추적 문서 3건을 보존했다.
- Stage A exact Web 4경로 diff는 0이다.
- 허용 밖 `local_service.rs`에 생긴 rustfmt 기계 변경은 즉시 HEAD로 복원했다.

## 미검증·금지 준수

- 실제 Windows 설치·Native Login·Vault Credential 사용·운영 Gateway Network·PDF upload·질문·Citation·보고서 생성은 수행하지 않았다.
- 배포·NSIS·Browser·DB Migration·Backup·Restore·Repair·Commit·Push를 수행하지 않았다.
- 자동 테스트 결과를 실제 Windows/운영 PASS로 승격하지 않는다. 실제 Journey는 승인 계획 Task 8의 별도 Go/No-Go 대상이다.

## 독립 재검토

- Spec compliance: PASS.
- Code quality: APPROVED.
- Critical 0건, Important 0건.
- 이전 Important 4건(Session 수명, DTO/metadata/status, Idempotency, 실행 테스트)은 모두 ADDRESSED 판정이다.
- 비차단 Minor 1건: 25MiB PDF IPC 다중 복사 비용은 Task 8 실제 Windows Journey에서 메모리·응답시간을 측정한다. Stage C 재개 사유는 아니다.
- 검토자는 테스트를 직접 재실행하지 않았으므로 실행 원문은 Cannot verify이며, 어울1의 최신 실행 증거는 Node 32/32·Rust 103/103·lint/build/Product Gate PASS다.

## 다음 판단

- Stage C는 기술 수락한다. Commit·Push는 신산님의 별도 승인 판단에 따른다.
- 이후 Task 8 배포·실제 Windows Journey는 신산님의 별도 사전 승인을 받는다.

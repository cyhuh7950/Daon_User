# R1 사용자 제품 분리 Stage C 작업지시서

## 1. 목적

승인 계획 Task 7만 수행해 Windows 사용자 프로그램을 Native Credential Vault와 고정 Gateway 기반의 실제 Product Workspace 7종 Command에 연결한다.

## 2. 범위·금지

- 공식 정본은 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`이다.
- Production 배포, Chrome, NSIS Build·설치, 실제 자격증명·Backup·Restore·Repair는 수행하지 않는다.
- Evidence Hub와 Web Adapter·Studio API 공개 계약을 변경하지 않는다.
- Browser/WebView에 Access·Refresh·Password, Gateway, Authorization, 내부 URL을 공개하지 않는다.
- 사용자 기존 삭제·Cargo 표시·Native Evidence·기존 미추적 문서를 보존한다.
- Commit·Push는 어울1이 수행한다.

## 3. 허용 파일

- `apps/desktop/src-tauri/src/workspace_bridge.rs`
- `apps/desktop/src-tauri/src/native_session.rs`
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/tests/workspace_bridge_contract.rs`
- `apps/desktop/src/windows-workspace-adapter.js`
- `apps/desktop/src/desktop-shell.jsx`
- `packages/ui/src/product-workspace-shell.jsx`
- `scripts/run-isolated-desktop-cargo.mjs`
- `scripts/tests/windows-workspace-adapter.test.mjs`
- `scripts/tests/desktop-tauri-shell.test.mjs`
- `scripts/tests/product-workspace.test.mjs`
- 본 작업 Progress·Completion 문서

필요한 기존 Native Session·HTTP Transport 경계가 실제 코드와 충돌하면 범위를 임의 확대하지 말고 증거와 함께 어울1 판단을 요청한다.

## 4. 구현 계약

### 4.1 정확한 Command Surface

- `workspace_list_sources`
- `workspace_upload_pdf`
- `workspace_processing_status`
- `workspace_ask_question`
- `workspace_citation_content`
- `workspace_create_report`
- `workspace_list_studio_outputs`

WebView 입력은 각 Command 전용 `deny_unknown_fields` DTO만 허용하고 `method`, `path`, `url`, `gateway`, `authorization`, Credential을 거부한다.

### 4.2 Native 보안 경계

- Access는 Native Vault에서 Rust 내부로만 읽고 응답·Debug·Error·Event에 포함하지 않는다.
- `native_session.rs`에는 crate-private 고정 Workspace operation enum과 전용 authenticated executor만 추가한다. Credential getter, raw Access 반환, 임의 URL·Method·Path 또는 범용 credential callback은 금지한다.
- `workspace_bridge.rs`는 검증된 전용 DTO를 operation enum으로 전달하고 Safe response만 받는다. Access와 Authorization Header 소유·삭제는 `native_session.rs` 내부에서 끝난다.
- 고정 HTTPS Gateway와 고정 Path/Method만 사용한다.
- 기존 Native Session의 redirect none, connect/total timeout, JSON 응답 128KiB 상한, Content-Type·status·exact DTO, Secret owner/zeroize 경계를 재사용한다.
- Citation PDF 응답은 JSON 한도와 분리해 `application/pdf`, PDF signature, 최대 25MiB를 엄격 검증하고 그 밖의 bytes는 Safe fail-close한다.
- Session 없음·Workspace mismatch·입력 오류는 network 0으로 fail-close한다.
- Upload는 PDF filename·MIME·size·bytes를 엄격 검증하고 허용 상한을 넘기면 network 0이다.
- Write는 자동 replay하지 않는다. Idempotency Key는 Rust에서 16~128 Safe 값으로 생성·요청 fingerprint에 결속한다.

### 4.3 JS Adapter·Shell

- `WindowsWorkspaceAdapter`는 Stage B `ProductWorkspaceAdapter` 7개 메서드를 구현한다.
- 기존 7개 필수 메서드와 `citationUrl` 의미는 보존한다. Windows Adapter만 선택적 async `citationContent(citation)` capability를 제공하며 공용 Shell은 이 capability가 있을 때 Citation 클릭의 기본 이동을 중단하고 Native bytes로 `blob:` PDF URL을 열어 page fragment를 적용한다.
- Web Adapter는 `citationContent`를 구현하지 않으며 기존 same-origin Citation anchor 동작을 그대로 유지한다. Windows Native PDF bytes와 생성한 object URL은 작업 수명 뒤 revoke하고 State·로그·Error에 보존하지 않는다.
- 하나의 인증 Session 수명에서 Adapter 한 인스턴스만 만들고 `ProductWorkspaceShell`에 주입한다.
- unauthenticated/logout/session change 시 이전 Adapter 결과를 재노출하지 않는다.
- WebView Source에는 `fetch`, Gateway, localhost/loopback, Authorization, Credential 문자열이 없어야 한다.

## 5. TDD 순서

1. Node exact Command·unknown input·secret/network 금지 RED.
2. Rust Session 없음/Workspace mismatch/network0, 7 Method/Path/DTO, upload bounds, redirect/timeout/oversize/malformed RED.
3. 최소 Rust Bridge·State·7 Command·handler 등록 GREEN.
4. Windows JS Adapter·DesktopShell 실제 React 연결 RED→GREEN.
5. focused → isolated Cargo 전체 → Desktop lint/build → Product Gate → diff 순으로 fresh 검증.

## 6. 필수 검증

```powershell
node --test scripts/tests/windows-workspace-adapter.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/product-workspace.test.mjs
node scripts/run-isolated-desktop-cargo.mjs test
npm run verify:desktop-lint
npm run verify:desktop-build
npm run verify:product-ui-boundary
git diff --check
```

자동 검증은 실제 Windows 설치·로그인·Network PASS를 대신하지 않는다.

## 7. 완료 보고

`docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-STAGE-C-01_completion_report.md`에 RED→GREEN, Command·보안 경계, 테스트 수치, Dirty 보존, 미검증 Task 8을 기록한다.

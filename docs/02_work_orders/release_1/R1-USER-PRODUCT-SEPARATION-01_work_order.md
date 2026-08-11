# R1 사용자 제품과 Evidence Hub 분리 Stage A 작업지시서

## 1. 승인 기준과 Writer

- Work Order ID: `R1-USER-PRODUCT-SEPARATION-01`; Issue ID: `R1-USER-PRODUCT-SEPARATION-01-I001`.
- 상태: `READY` · 2026-08-11.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; 승인 기준 HEAD: `2514b64f0f552537b4b8b39adc9fa5cd0632afb3`.
- 신산님은 2026-08-11 설계·계획과 Web 핵심 4경로 제한 복원, 어울2 단일 Writer 실행을 명시 승인했다. Branch·Worktree를 생성하지 않는다.
- 착수 전 `AGENTS.md`, `docs/superpowers/specs/2026-08-11-evidence-hub-product-separation-design.md`, `docs/superpowers/plans/2026-08-11-evidence-hub-product-separation.md`의 Global Constraints와 Task 0~3을 EOF까지 읽고 SHA-256·적용 조항을 진행 기록에 남긴다.
- 어울2가 이 작업 범위의 유일 Writer다. 어울1은 구현 중 같은 범위를 수정하지 않는다.

## 2. 단일 목표와 완료 조건

- 목표: 개발·검증용 Evidence Hub를 로그인 없는 `apps/evidence-hub` 로컬 전용 앱으로 분리하고, Web·Windows 제품의 Evidence Home을 실제 사용자 인증 진입 경계로 교체한다.
- Evidence Hub는 기존 M2 Fixture·Manifest·Journey 계약을 보존하지만 외부 API·DB·Upload·Recovery·Session 발급을 수행하지 않는다.
- Web 비인증 `/`는 가입·로그인 UI만 표시하고 Evidence Hub를 표시하지 않는다.
- Windows 비인증 상태는 독립 Native Login 화면만 표시하며, 인증 성공 후 기본 Route는 `WorkspaceDetail`이다.
- Stage A는 실제 Source·질문·Citation·Studio API를 새로 구현하지 않는다. 실제 연결 전 Workspace는 가짜 성공 없이 `loading|empty|ready|error|forbidden|unavailable` Safe 상태만 사용한다.
- 제품 Source와 Build 산출물에서 `ProductionBoundEvidenceHub`, `prototype_fixture`, `deferred_actual`, `Mock Adapter`, `@daon-user/evidence-hub`를 0건으로 만든다.

## 3. 구현 계약

### 3.1 제한 복원

다음 4경로만 HEAD 원본으로 복원한다.

- `apps/web/app/api/v1/[...path]/route.js`
- `apps/web/app/bff/api/[...path]/route.js`
- `apps/web/app/bff/shell/runtime/route.js`
- `apps/web/app/workspaces/[workspace_id]/page.jsx`

복원 후 각 경로의 `git diff`는 0건이어야 한다. 나머지 사용자 삭제 27건과 원 미추적 문서 3건, 기존 Native Evidence/보고서는 보존한다. `git reset`, `git clean`, 전체 `git restore`를 금지한다.

### 3.2 TDD 순서

1. 승인 4경로 제한 복원과 나머지 Dirty 보존을 먼저 증명한다.
2. Evidence 앱 부재, 제품 UI Export 잔존, Web·Desktop Evidence import 잔존을 RED로 고정한다.
3. 기존 Evidence Pane·Model을 내용 보존 이동하고 별도 Vite 앱과 `dev:evidence-hub`·`verify:evidence-hub`를 연결한다.
4. Product Source·Bundle 금지 Token 검증기를 RED→GREEN으로 추가한다.
5. 공용 `ProductWorkspaceShell`은 Fixture를 생성하지 않는 Safe 상태 모델만 제공한다.
6. Web `/`와 Windows 비인증/인증 진입을 실제 React 행동 테스트로 교정한다.
7. Windows 권한 없는 Organization·Operations는 Navigation과 Handler 양쪽에서 실행 0건이어야 한다.

### 3.3 허용 변경 경로

- Restore exact 4 paths: §3.1의 Web 파일
- Create: `apps/evidence-hub/**`
- Modify: `packages/ui/src/index.js`
- Move/Delete after content preservation: `packages/ui/src/production-bound-evidence-pane.jsx`
- Move/Delete after content preservation: `packages/ui/src/production-bound-evidence-model.js`
- Create: `packages/ui/src/product-workspace-shell.jsx`
- Create: `packages/ui/src/product-workspace-model.js`
- Modify: `apps/web/app/page.jsx`
- Modify: `apps/web/app/layout.jsx`
- Modify: `apps/web/lib/auth-pane.jsx`
- Modify: `apps/web/components/actual-workspace.jsx`
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `apps/desktop/src/desktop-shell-model.js`
- Modify: `apps/desktop/src/desktop-shell.css`
- Modify: `apps/desktop/src/native-auth-panel.jsx`
- Modify: `package.json`
- Modify: `package-lock.json` only for the new workspace link; dependency versions must not change
- Modify: `quality-gate-policy.json`
- Modify: `scripts/build-local-service-sidecar.mjs`
- Modify: `scripts/run-isolated-desktop-cargo.mjs`
- Create: `scripts/verify-product-ui-boundary.mjs`
- Create/Modify: `scripts/tests/evidence-hub-boundary.test.mjs`, `scripts/tests/product-ui-boundary.test.mjs`, `scripts/tests/platform-prototype-evidence.test.mjs`, `scripts/tests/workspace.test.mjs`, `scripts/tests/desktop-tauri-shell.test.mjs`, `scripts/tests/windows-recovery-adapter.test.mjs`
- Append/Create: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-01_progress.md`
- Create: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-01_completion_report.md`

Rust 제품 코드, API/OpenAPI, DB/Migration, Native Credential·Recovery, 배포 설정은 변경하지 않는다. 실제 Source·질문·Studio 연결은 후속 Stage B 작업지시 책임이다.

## 4. 기존 기능과 보안 보호

- Evidence Hub의 기존 8 Journey·4 Client·Negative State·Reconciliation 계약은 보존한다.
- Evidence 앱은 `sessionStorage` 외 Storage, `fetch`, XHR, WebSocket, Tauri invoke, auth/recovery/upload를 사용하지 않는다.
- Browser 제품은 same-origin 상대 경로만 사용하고 내부 API 주소를 노출하지 않는다.
- Desktop CSP `connect-src 'none'`, Native Session Vault, Local Service, Recovery Command를 변경하지 않는다.
- Password·Credential·Authorization·내부 URL·Loopback Port를 UI, Error, Log, State에 보존하지 않는다.
- 요구되지 않은 리팩터링·전체 재작성·의존성 Version 변경을 금지한다.

## 5. 필수 검증

```powershell
node --test scripts/tests/evidence-hub-boundary.test.mjs scripts/tests/platform-prototype-evidence.test.mjs
node --test scripts/tests/product-ui-boundary.test.mjs
node --test scripts/tests/workspace.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/windows-recovery-adapter.test.mjs
npm run build --workspace @daon-user/evidence-hub
npm run verify:desktop-build
npm run verify:product-ui-boundary
npm run verify:desktop-lint
git diff --check
```

- 실제 React Harness에서 Windows 비인증 Login-only, 인증 후 Workspace 기본 Route, 권한 없는 메뉴·Handler 0회를 검증한다. Regex만으로 완료하지 않는다.
- Product Source와 Build Bundle을 모두 Scan한다. Evidence 앱 내부의 금지 Token은 허용하지만 제품 Import Graph에 포함되면 실패해야 한다.
- `npm run verify:workspace`는 마지막에 실행하되 보존된 사용자 삭제 Web 설정 Route 파생 실패를 이번 변경 실패와 분리한다.
- Build·자동 테스트는 실제 Web Production Chrome·Windows NSIS 사용자 여정 PASS를 대신하지 않는다.

## 6. 진행·결과 계약

- 진행 기록: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-01_progress.md`.
- 착수, 제한 복원, 각 RED, 각 GREEN, 오류·원인·복구, 검증, 종료 직전에 시각·상태·변경 파일·명령·결과·다음 작업을 즉시 기록한다.
- 완료 보고: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-01_completion_report.md`.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
- Commit·Push·배포·Browser·Installer는 어울1 소유이므로 수행하지 않는다.


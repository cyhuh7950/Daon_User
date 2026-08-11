# R1-M5-07 Windows React Recovery Adapter C10-R02 진행 기록

## 2026-08-11 KST — S0 2차 재작업 인수

- 단계·상태: `S0` · `IN_PROGRESS`
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch `master`; HEAD `bc4ea833a897086ead6da29cd21f5bce6cb79085`; origin `git@github-cyhuh7950:cyhuh7950/Daon_User.git`.
- 적용 문서: C10-R02 작업지시서·프롬프트와 C10-R01 작업지시·Progress·Completion을 EOF까지 확인했다.
- 검토 판정 확인: Adapter Trace가 `A-Za-z0-9._:-` 1~256자를 허용해 Rust 정본의 정확한 32자리 소문자 hex보다 넓다. R01 테스트는 Helper/정적 계약 중심이라 제품 React Tree의 Login·Authz·Remount·Logout 경쟁을 직접 렌더하지 않았다.
- 사용 가능 도구: 현재 Lock/Workspace에는 React·ReactDOM 19.2.7과 Vite가 있으며 react-test-renderer/jsdom/testing-library는 없다. 새 의존성 없이 Vite로 실제 제품 JSX를 임시 Bundle하고 최소 DOM Event Harness에서 ReactDOM `createRoot`·`act`를 사용한다.
- Dirty 보호: 승인 C10B/R01, 원 C10/C10-R01 변경·보고, 사용자 추적 삭제 31건과 원 미추적 문서 3건을 보존한다.
- 다음 작업: Trace 거부와 실제 React 네 행동을 먼저 RED로 고정한다.
- 금지 준수: API·Rust·Dependency·Lock·Commit·Push·배포·Browser·설치·실제 Login/Recovery 0건.

## 2026-08-11 KST — S1 Trace 계약 RED

- 단계·상태: `S1` · `RED_CONFIRMED`
- 변경 파일: `scripts/tests/windows-recovery-adapter.test.mjs`.
- 추가 계약: 정상 Safe Trace를 `0123456789abcdef0123456789abcdef`로 고정하고 Credential 유사·짧은 값·대문자·점·콜론·밑줄·하이픈 포함 Trace 7종이 원문 반사 없이 `CLOUD_RECOVERY_RESPONSE_REJECTED`가 되는 테스트를 추가했다.
- 명령·결과: `node --test scripts/tests/windows-recovery-adapter.test.mjs` → 7건 중 6 PASS, 신규 Trace 테스트 1 FAIL. 첫 Credential 유사 Trace가 현재 `FORBIDDEN`으로 통과해 RED 원인을 재현했다.
- 오류·원인: 제품 Adapter의 Trace 정규식이 `^[A-Za-z0-9._:-]{1,256}$`로 과도하게 넓다.
- 다음 작업: 실제 React 행동 RED를 추가한 뒤 제품 Trace 정규식과 테스트 가능 주입 경계를 최소 보정한다.

## 2026-08-11 KST — S2 실제 React 행동 RED

- 단계·상태: `S2` · `RED_CONFIRMED`
- 변경 파일: `scripts/tests/desktop-tauri-shell.test.mjs`.
- Harness: 현재 의존성의 React 19·ReactDOM `createRoot/act`와 Vite 실제 JSX Bundle을 사용하고, 새 의존성 없이 최소 DOM Event Harness를 구성했다. 테스트마다 singleton fake Tauri invoke를 제품 `DesktopShell` Tree에 전달한다.
- 초기 Harness 오류·복구: Vite 산출 확장자, React `act` import 시점, Select options, Storage, `getElementById`, boolean attribute를 순차 보정했다. 이는 제품 판정에 포함하지 않았다.
- 제품 RED: 보정 Harness에서 주입한 watch scheduler가 호출되지 않아 `제품 Tree가 주입된 watch scheduler를 사용해야 한다`로 실패했다. `DesktopShell`이 invoke/watch 경계를 받지 않는 실제 공백을 확인했다.
- 다음 작업: Trace exact regex, 제품 Tree invoke/watch 주입, Remount 식별 증거를 최소 GREEN으로 적용한다.

## 2026-08-11 KST — S3 최소 제품 GREEN·행동 확장

- 단계·상태: `S3` · `GREEN`
- 변경 파일: `apps/desktop/src/windows-recovery-adapter.js`, `apps/desktop/src/desktop-shell.jsx`, `apps/desktop/src/native-auth-panel.jsx`, `scripts/tests/windows-recovery-adapter.test.mjs`, `scripts/tests/desktop-tauri-shell.test.mjs`, `scripts/tests/operations-recovery.test.mjs`.
- Trace: Safe Trace 정규식을 `^[0-9a-f]{32}$`로 축소했다. Credential 유사·짧음·대문자·구분자 포함 7종은 원문 반사 없이 기존 Cloud Response Rejected로 닫힌다.
- 실제 React 주입: `DesktopShell`에 선택적 `nativeInvoke`·`sessionWatchOptions`를 주입하고 Session Bridge와 Recovery Adapter가 동일 invoke를 사용한다. 운영 호출은 prop 부재 시 기존 Tauri global 경계를 유지한다.
- Auth UI: Login ID와 Password를 uncontrolled DOM ref로 사용하고 Password는 submit 직후 지운다. React State·Storage·Log에 Password를 보존하지 않는다.
- Remount 증거: Shell에 `data-session-tree-key`를 투영하고 Session/Authorization revision에 따른 Operations Tree 교체를 실제 DOM에서 검증한다.
- 실제 React 행동: Login reject는 Password clear·unauthenticated·Recovery invoke 0, Login success는 exact7→`session-1:5`→Cloud list 1회, Authorization `[]`/reject는 렌더된 Cloud 버튼 click invoke 0, Logout 후 늦은 Authorization/Poll은 이전 Session·권한·결과 재노출과 추가 Recovery invoke 0을 확인했다.
- Handler 직접 경로: Cloud 7 operation 각각을 `[]`, null, 부분, unknown 권한에 넣어 총 28개 거부 경로 모두 invoke 0을 확인하도록 확장했다.

## 2026-08-11 KST — S4 Fresh 검증

- 단계·상태: `S4` · `VERIFIED_WITH_PRESERVED_FAILURES`
- 필수 Node 3파일: `node --test scripts/tests/windows-recovery-adapter.test.mjs scripts/tests/operations-recovery.test.mjs scripts/tests/desktop-tauri-shell.test.mjs` → `55/55 PASS`. 실제 React 행동 1건 안에 승인된 네 시나리오를 모두 실행했다.
- 구문: 변경 JS/MJS 5파일 `node --check` PASS.
- Lint: `npm run verify:desktop-lint` → 4파일 PASS. R02 제품 6파일 직접 `lint-workspace` → PASS.
- Lint 범위 보정: 제품+테스트를 한 번에 등록한 시도는 보안 Fixture 문자열을 금지 패턴으로 올바르게 탐지해 실패했다. 제품 6파일 정식 범위로 재실행해 PASS했으며 Fixture나 Lint 규칙은 완화하지 않았다.
- Workspace: `npm run verify:workspace` → `31/35 PASS`. 4 FAIL은 보존된 사용자 삭제 `apps/web/app/workspaces/[workspace_id]/page.jsx` 부재 파생으로 C10-R01과 동일하다.
- Scan: 제품 6파일 Network/internal address 금지 0건, Auth/Adapter 3파일 Credential·Storage·Log 금지 0건, 신규 범용 Recovery Error 0건.
- Diff: `git diff --check` PASS. 실패 Harness가 만든 exact `.c10-r02-react-*` 임시 폴더 2개만 검증 후 삭제했고 현재 잔존 0건이다.
- Dirty: `master` HEAD `bc4ea833a897086ead6da29cd21f5bce6cb79085`, 사용자 삭제 31건과 원 미추적 문서 3건, 승인 C10B/R01·원 C10/R01 변경을 보존했다.
- 금지 준수: API·Rust·Dependency·Lock 수정, Commit·Push·배포·Browser·설치·실제 Login/Recovery 0건.

## 2026-08-11 11:58 KST — S5 종료

- 단계·상태: `S5` · `COMPLETED`
- 결과: C10-R02 Trace·실제 React 행동 계약을 모두 충족했다.
- 다음 작업: 어울1의 Diff·증거 독립 검토와 C10-R02 기술 수락 판단.

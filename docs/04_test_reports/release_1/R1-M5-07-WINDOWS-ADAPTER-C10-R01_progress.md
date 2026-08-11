# R1-M5-07 Windows React Recovery Adapter C10-R01 진행 기록

## 2026-08-11 KST — S0 재작업 인수·기준선 확인

- 단계·상태: `S0` · `IN_PROGRESS`
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch `master`; HEAD `bc4ea833a897086ead6da29cd21f5bce6cb79085`; origin `git@github-cyhuh7950:cyhuh7950/Daon_User.git`.
- 적용 문서: `AGENTS.md`, Windows Recovery 설계 1.3, Native Bridge Plan Task 5·Completion Contract, C10B/C10B-R01 Completion, 원 C10 Completion, C10-R01 작업지시서·프롬프트를 EOF까지 확인했다.
- 계약 확인: Native Session outer/session exact-key, Recovery Authorization exact-key·빈 배열 또는 정렬된 Cloud 7종 전체, Rust Safe Error 보존, Cloud 버튼·Handler 이중 차단, Native login/logout UI와 Password 비보존을 동일 Issue에서 보정한다.
- Dirty 보호: 승인 C10B/R01 API·Rust/OpenAPI 변경, 원 C10 변경, 사용자 추적 삭제 31건과 원 미추적 문서 3건을 확인했다. 허용 경로 밖 파일은 수정·복원·Stage하지 않는다.
- 문서 SHA-256: AGENTS `AABB1117...B47EA`; 설계 `86634AD2...E8307A`; Plan `BDCA37ED...FEC6EA`; C10B `9D49AD88...A812C0`; C10B-R01 `CCFF7DC0...238CB6`; C10 `1F0DCFE9...8BE70`; R01 Work Order `27168CDD...F074F`; Prompt `F9631B15...59D26C`.
- 현재 확인된 결함: Session Bridge가 unknown/credential session key를 허용하고 unauthenticated outer key를 축약한다. Adapter가 신규 범용 공개 Error code를 만들며 Rust Safe Error 일부를 손실한다. Cloud UI는 권한 Projection을 받지 않고 mount 시 목록을 호출한다. 실제 Native 로그인·로그아웃 UI가 없다.
- 다음 작업: 네 결함을 행동 테스트로 먼저 고정하고 각각 기대 원인의 RED를 확인한다.
- 금지 준수: API·Rust·Cargo·Lock·DB·CSP 수정, Commit·Push·배포·Browser·실제 Login/Recovery 0건.

## 2026-08-11 KST — S1 Important 4건 행동 RED

- 단계·상태: `S1` · `RED_CONFIRMED`
- 변경 파일: C10 Node 테스트 3파일만 수정했다. 아직 제품 코드는 수정하지 않았다.
- 실행: `node --test scripts/tests/windows-recovery-adapter.test.mjs scripts/tests/operations-recovery.test.mjs scripts/tests/desktop-tauri-shell.test.mjs`.
- 결과: `46 PASS / 6 FAIL`. 여섯 실패는 모두 승인된 결함을 직접 검출했다.
  - Native Auth Panel 파일 부재.
  - Handler용 `invokeAuthorizedCloudRecovery` 부재.
  - Unknown Rust Error가 신규 `RECOVERY_COMMAND_FAILED`로 변환됨.
  - 승인 Rust Trace와 Safe Error가 손실됨.
  - `recoveryAuthorizationStatus` strict Projection 부재.
  - Password 즉시 삭제·Logout 선제 정리 실행 함수 부재.
- RED 품질: 실패는 오탈자나 Fixture 오류가 아니라 필요한 제품 함수·UI·계약의 부재와 기존 잘못된 공개 Error 동작 때문이다.
- 다음 작업: Session/Authz strict Projection과 Safe Error를 먼저 최소 GREEN으로 만들고, 권한 Guard와 Native Auth Panel·Shell remount를 순차 연결한다.
- 막힘: 없음.

## 2026-08-11 KST — S3 최종 검증·종료

- 단계·상태: `S3` · `COMPLETED`
- 추가 보안 회귀: Logout Native 응답 대기 중 Poll이 직전 인증 Session을 재노출하는 경쟁 조건을 행동 RED로 확인하고, Bridge logout fail-close latch로 보정했다. 로그인 성공 전까지 인증 Poll을 unauthenticated로 유지한다.
- 필수 Node 회귀: `53/53 PASS`.
- 구문·Lint: 신규 JS 2파일 `node --check` PASS, `npm run verify:desktop-lint` PASS, C10-R01 제품 6파일 직접 Workspace Lint PASS.
- Workspace 회귀: `31/35 PASS`, 4 FAIL. 모두 사용자 보존 삭제 `apps/web/app/workspaces/[workspace_id]/page.jsx` 부재의 기존 ENOENT·Route/Token 파생 실패다.
- 보안 Scan: Browser Network/내부 주소 0건, Access·Refresh·API Key·Bearer·Gateway·Storage·Console 0건, Password State/Storage/Log 0건, 신규 범용 Recovery Error code 0건.
- Diff/Dirty: `git diff --check` PASS. `master` HEAD `bc4ea833a897086ead6da29cd21f5bce6cb79085`, 사용자 삭제 31건·원 미추적 문서 3건 보존. 승인 C10B/R01 API·Rust·OpenAPI 변경과 원 C10 기록을 수정하지 않았다.
- 미수행: Commit·Push·배포·Browser·설치·실제 Login/Backup/Restore/Repair 0건.
- 다음 작업: 어울1이 최신 Diff·실행 증거를 독립 검토해 C10-R01 기술 수락을 판단한다. 실제 Windows 설치형 판정은 승인 Plan Task 6에서 별도 수행한다.

## 2026-08-11 KST — S2 최소 GREEN·권한/UI 연결

- 단계·상태: `S2` · `GREEN`
- Session/Authz: outer `authenticated,session`, authenticated session 6개 snake_case key, unauthenticated `false,null`, Authorization `recovery_operations` 단일 key를 exact 검증한다. 권한은 빈 배열 또는 Rust 정본의 정렬된 Cloud 7종 전체만 허용한다.
- Safe Error: Cloud·Local 승인 Rust Error를 exact `code,trace_id,retryable`로 보존한다. Unknown key/code/Trace는 기존 `CLOUD_RECOVERY_RESPONSE_REJECTED` 또는 `LOCAL_RECOVERY_RESPONSE_REJECTED`로 닫고 신규 범용 공개 Error code를 제거했다.
- 권한 이중 차단: Windows Cloud 버튼 disabled 조건과 Handler 실행 Guard가 같은 exact Projection을 확인한다. 권한 없음·부분/Unknown Projection에서는 Adapter invoke를 호출하지 않는다. Web 기존 Cloud Adapter와 Local 3종 독립 계약은 보존한다.
- Native Auth UI: `native-auth-panel.jsx`를 추가해 Login ID·uncontrolled Password·로그인·로그아웃·Safe Session 상태를 제공한다. Password input은 Native invoke 대기 전에 즉시 비우고 State·Storage·Log에 넣지 않는다. Logout은 Native 응답 전에 Session Tree를 비운다.
- Shell: singleton Bridge/Adapter를 유지하며 시작·로그인·Session 변경·Operations 진입에 최신 Authorization을 조회한다. pending/success/failure마다 고유 Revision으로 Operations Tree를 재생성하고 실패 시 Cloud 권한을 빈 배열로 닫는다.
- 중간 검증: 필수 Node 3파일 `52/52 PASS`. 신규 remount Revision 충돌 회귀는 RED 1/1 후 GREEN으로 보정했다.
- 변경 범위: 허용 Desktop JS/JSX, 공용 Operations Pane/Model, Node 테스트, R01 Progress만 변경했다. API·Rust·C10B 변경은 수정하지 않았다.
- 다음 작업: 구문·Lint·Workspace·Diff·Network/Secret scan과 Dirty 보존을 fresh 검증하고 Completion을 작성한다.
- 막힘: 없음.

# R1-M3-06-C14 수정 작업지시서 — iOS UI 조기 종료의 Fail-close 진단과 원인 한정 복구

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I005` |
| Attempt | `15` |
| 사유 | unsigned Build 성공 뒤 실제 UI Test 3개에서 앱이 실행 중이 아니거나 Crash로 종료됐으나 현재 Artifact가 종료 원문을 독립 판정할 진단 파일을 제공하지 않음 |
| 실패보고 | 0회 · 처음 도달한 Runtime/XCTest 결함이며 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-15.md` |

## 2. 확인된 증거

- PR Head는 `26045362e83472b20e194566d75902fb6e2e9a84`, Pull Request merge candidate 실제 Checkout/GitHub SHA는 `c5c882416d2c93d1ba5395118be8be095ed89df6`다.
- Run `30237187483`, Job `89887116611`, Artifact `8642229194`에서 Checkout·Node/npm/uv·CocoaPods `1.16.2`·Portable 회귀·Pods 재현 설치·exact Simulator 생성·Xcode 26.6 `CompileStoryboard`·unsigned Build는 모두 성공했다.
- `testApprovedNavigationRoutesAreClickable`은 `Home 화면 열기` 탐색 뒤 앱이 실행 중이 아니라며 실패했다.
- `testForegroundBackgroundAndRelaunchPreserveApprovedRoute`도 `Notifications 화면 열기` 탐색 뒤 앱이 실행 중이 아니라며 실패했다.
- `testPermissionControlsAndSettingsBoundary`은 세 권한 버튼을 찾지 못했고 마지막에는 `com.sinsan.daon crashed in <external symbol>`로 종료됐다.
- 일반 UI Suite는 3 Test·5 Failure, Exit 65다. 권한 3단계와 후속 Simulator 검증은 fail-close로 미실행됐다.
- Evidence Manifest는 `FAILED`, `failed_steps:["ui_tests"]`, Build 성공, Simulator 검증 skipped와 누락 `.xcresult` 3개를 정확히 기록했다. Simulator shutdown/delete와 Artifact Upload도 성공했다.
- 현재 Artifact는 `DaonUITests.xcresult`를 포함하지만 Windows에서 Xcode 전용 결과 저장소의 Crash 종료 원문을 독립 추출할 수 없다. 로컬 추출 경로는 `C:/tmp/Daon_User-r1-m3-06-artifact-30237187483/.../DaonUITests.xcresult`다.
- 공통 Quality Gate Run `30237187436`은 같은 PR Head에서 성공했다.

## 3. 필수 작업

### A. 실패 시 진단 증거를 보존한다

1. UI Test Step에서 `xcodebuild test`의 원래 Exit Code를 보존한다.
2. 성공·실패와 무관하게 Test 종료 뒤 다음을 `artifacts/ios-phase-a/evidence/diagnostics/` 아래에 남긴다.
   - 현재 Xcode가 지원하는 `xcresulttool` 명령으로 Test Result Summary와 가능한 Failure/Attachment 내보내기
   - 해당 exact Simulator의 Daon Process Unified Log. 범위는 UI Test 실행 구간으로 한정하고 비밀·전체 환경 Dump를 금지한다.
   - 생성된 Daon Crash/Diagnostic Report가 있으면 원본과 파일 목록
   - Test 명령 Exit Code와 진단 수집 각 명령의 성공/실패 상태
3. 진단 수집 실패가 원래 Test 실패를 성공으로 바꾸지 않게 하고, 원래 Exit Code로 Step을 종료한다.
4. Simulator shutdown/delete 전 진단 수집이 완료되도록 Workflow 순서를 보장한다.
5. Artifact Upload는 기존 Fail-close 계약을 유지하며 위 진단 파일을 포함한다.

### B. 원인 한정 복구 원칙

1. 현재 Run 로그·Artifact·Source에서 Crash 원인이 단일하게 확인되면 그 원인만 최소 수정한다.
2. 현재 증거만으로 단일 원인을 입증할 수 없으면 Product 동작을 추측 수정하지 말고 진단 증거 보강까지만 완료한다. 이 경우 결과 상태는 `IMPLEMENTED_PENDING_MACOS_DIAGNOSTIC_RUN`으로 보고한다.
3. UI Test는 앱 Root Shell이 준비되고 Process가 runningForeground인지 먼저 확인해, 버튼 탐색·Swipe 실패가 Root Crash를 가리지 않도록 한다. 단, Retry로 Crash를 숨기거나 실패를 Skip하지 않는다.
4. Bundle ID, Deep Link 8 Route, Permission 계약, Lifecycle 보존, Signing 금지, Release Build와 기존 Test 시나리오의 합격 의미를 완화하지 않는다.

### C. TDD와 회귀

1. Workflow에 원래 Exit Code 보존, always 진단 수집, 진단 후 동일 실패 반환, Cleanup 전 수집 순서를 계약 Test로 선고정한다.
2. Xcode 전용 명령은 현재 Xcode 26.6 명령 도움말/공식 출력 형식에 맞추고 지원되지 않는 Legacy 구문을 추측하지 않는다.
3. Test Root Readiness는 접근성 Root와 Process 상태를 검증하고 기존 Route·Lifecycle·Permission Test를 삭제·Skip·완화하지 않는다.

## 4. 완료 조건

- C14 계약 RED→GREEN
- iOS Root Gate, Evidence Fixture, Workflow JSON/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- 진단 수집 오류가 Test 실패를 은폐하지 않고 Simulator Cleanup·Artifact Upload를 방해하지 않는 Fixture PASS
- Signing·Bundle ID·Deep Link·Permission·Lifecycle·Android Native·공개 API·데이터 계약 변경 0
- 개인 절대경로·Generated Pods/Build/Gem/Test Temp·Signing Asset 잔존 0
- Progress·Attempt 15에 Run/Job/Artifact, PR Head/실제 Checkout SHA, 3 Test·5 Failure와 진단 한계를 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속


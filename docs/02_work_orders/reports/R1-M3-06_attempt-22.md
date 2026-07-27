COMPLETED | R1-M3-06-I007 | C21 Route Wait 공통 실패 진단 경계 구현 | 승인 Route 기반 Evidence 파일·허용 Marker 요약·원 Exit 보존 Fixture·Progress·Attempt 22 생성 | iOS 37/37·Mobile 전체·Android 11/11·Node 300/300·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA macOS Simulator 전체 검증 미실행 | 어울1의 Commit·Push와 macOS CI·Artifact 판정

# R1-M3-06 Attempt 22 결과보고

## 판정

C21 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 최초 Home, Warm Route 7종과 Lifecycle 복원 Route의 모든 `wait_for_route` 실패를 하나의 Fail-close 진단 경계로 통합했다. 실패 시 승인 Route만 파일명과 Step 요약에 사용하고 exact Daon Unified Log 전체는 Evidence 파일에 저장하며, Step Log에는 승인된 기대/실제 Route와 허용된 Native Pending·Route Saved·Lifecycle Marker만 출력한 뒤 원 Wait Exit를 반환한다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `a407daab3b216088aa6836d49f05a8368ba2ee63`의 Quality Gate Run `30246916501`은 성공했다.
- iOS Run `30246916481`은 unsigned Build와 UI Test 3개, 초기 Home 준비까지 성공했으나 첫 Warm URL `WorkspaceList` 뒤 저장 Route가 `Home`에 머물러 기존 20회 대기가 종료됐다.
- 기존 C20 구현은 최초 Home 실패에만 Unified Log를 남겨 Warm URL 수신 표식 `DAON_PENDING_DEEP_LINK_RECEIVED`와 저장 표식 `DAON_ROUTE_SAVED`의 선후를 확인할 수 없었다.
- Product/Native/AppDelegate 변경 없이 Simulator 검증 Script의 실패 관측 경계만 확장하면 승인된 기능·API·데이터 계약을 바꾸지 않고 다음 macOS 실행에서 정확한 실패 구간을 분리할 수 있다.

## 조치

### 변경 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
  - 승인 8 Route allowlist를 추가하고 기대 Route 검증 뒤에만 Evidence 파일명을 생성.
  - `wait_for_route_with_evidence`에서 원 Wait Exit를 보존하고 실패 시 `route-wait-failure-<approved-route>.log`에 exact Daon 최근 10분 Unified Log를 저장.
  - Step Log에는 `DAON_ROUTE_WAIT_EXPECTED`, 승인된 경우의 `DAON_ROUTE_WAIT_ACTUAL`, `DAON_PENDING_DEEP_LINK_RECEIVED`, 승인 Route의 `DAON_ROUTE_SAVED`, 승인 Lifecycle의 `DAON_LIFECYCLE_STATE`만 출력.
  - 최초 Home, Warm Route 7종, Lifecycle의 `AccountSettings` 대기를 공통 경계로 연결.
- `scripts/tests/ios-native-shell.test.mjs`
  - Home·WorkspaceList 실패 Fixture의 Exit 23 보존, Route별 Evidence 파일, 전체 Log 비노출과 허용 Marker 요약 계약.
  - 성공 Fixture의 다음 단계 계속과 실패 Evidence 미생성 계약.
  - 세 Route Wait 호출점과 기존 설치→종료→초기화→새 Launch→Home→Warm 순서 계약.
- Progress와 본 Attempt 22 보고서.
- 미변경: Product Source, Native/Bridge/AppDelegate/Xcode Project/UI Test, Evidence Writer/Workflow, Android, API, Route/Deep Link 계약, Sleep 1초·Wait 20회·Retry, URL 발송 방식, Rejected/Permission/Lifecycle 후속 순서, 최종 Log·Crash/Secret·Termination, Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C21 RED | iOS 34/37 PASS·3 FAIL: 공통 Helper와 Home·Warm·Lifecycle 호출점 부재 재현 |
| C21 GREEN | iOS 37/37 PASS |
| 실패 Fixture | Home·WorkspaceList 각각 Wait Exit 23 유지, Route별 전체 Unified Log Evidence 생성, 승인 기대/실제 Route와 허용 Marker만 Step 출력 PASS |
| 성공 Fixture | Inbox 성공 뒤 다음 단계 계속, 실패 Evidence 미생성 PASS |
| 기존 시나리오 | Install→Terminate→Clear→Launch→Home, Warm Route 7종, Rejected/Permission/Lifecycle, 최종 Log·Crash/Secret·종료 계약 유지 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 37/37, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C20과 동일 |
| 전체 Node | 300/300 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; 허용 파일 외 Product/Native/Bridge/Project/UI Test/Evidence/Workflow/Android/Lock/Pin Diff 0; Signing 변경 0; Pods/Build/Artifact 잔존 0 |

### 오류·복구 근거

- RED 34/37은 승인 C21의 공통 실패 진단 경계 부재를 재현한 예상 실패이며 나머지 iOS/Evidence/Deep Link 계약 34개는 모두 통과했다.
- `verify:mobile` 중 읽기 권한이 없는 기존 `services/local-service/.pytest_cache` 탐색 경고가 있었으나 검증 Exit는 0이고 관련 파일 변경은 없었다.
- 구현·Portable 회귀에서 기능 오류는 없었다.
- Windows에서는 macOS Unified Log의 실제 Marker 선후와 Simulator Warm URL Runtime을 검증할 수 없으므로 macOS 성공을 주장하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행해 UI Test 3개와 후속 Simulator Verification 전체를 판정한다.
3. 실패 시 `route-wait-failure-WorkspaceList.log`의 전체 Log와 Step 요약에서 Pending 수신→Route 저장→Lifecycle 순서를 확인한다.
4. 성공 Manifest와 Artifact 확인 전에는 iOS Phase A 완료로 판정할 수 없다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

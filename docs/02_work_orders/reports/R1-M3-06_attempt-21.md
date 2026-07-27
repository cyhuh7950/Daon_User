COMPLETED | R1-M3-06-I007 | C20 iOS Restore 정규화·초기 실패 Evidence 보존 | Restore 문자열 보존·null/undefined 정규화와 Home 실패 Daon Log·원 Exit Fixture·Progress·Attempt 21 변경 | iOS 37/37·Mobile 전체·Android 11/11·Node 300/300·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA macOS Simulator 전체 검증 미실행 | 어울1의 Commit·Push와 macOS CI·Artifact 판정

# R1-M3-06 Attempt 21 결과보고

## 판정

C20 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. Swift `nil`이 JavaScript Runtime에서 `undefined`로 전달될 수 있는 Restore Adapter 경계를 `null`로 정규화하고, 최초 Home 준비 실패 시 원래 실패 Exit를 유지하면서 exact Daon Unified Log를 Evidence에 남기도록 최소 수정했다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `50ccf34ec9e53c9bf560e62408d6c52539f7049b`의 Quality Gate Run `30245660436`은 성공했다.
- iOS Run `30245660449`에서 unsigned Build와 UI Test 3개, 초기 Process 종료와 새 Launch가 성공했으나 `wait_for_route Home`은 기존 20회 동안 빈 값으로 종료됐다.
- Native Adapter는 Promise 결과를 그대로 반환하고 App은 `restoredRoute === null`에서만 Home을 저장하므로 Runtime `undefined`는 준비 신호를 실행하지 않는다.
- 초기 Home 대기가 Fail-fast 종료돼 기존 최종 `simulator.log` 수집 전 구간의 Unified Log가 Artifact에 남지 않았다.
- C20 문서의 작업계획 파일명은 저장소에 없었으나 실제 승인 정본 `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` v0.9를 확인해 승인 경계를 대조했다.

## 조치

### 변경 범위

- `apps/mobile/src/platform/ios-host.ts`
  - Native Restore 반환 Type에 Runtime `undefined` 가능성을 반영.
  - Native 결과를 `await`하고 문자열만 그대로 반환하며 `null`·`undefined`를 포함한 비문자열은 `null`로 정규화.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - 최초 Home 대기의 Exit Code를 보존.
  - 실패 시 exact Simulator의 `process == "Daon"` 최근 10분 Unified Log를 `initial-home-failure.log`에 수집한 뒤 원 Exit로 종료.
  - 성공 시 기존 Warm Route와 최종 `simulator.log`·Crash/Secret 검증 흐름을 유지.
- `scripts/tests/ios-native-shell.test.mjs`
  - 문자열·null·undefined·비문자열 Restore Matrix 계약.
  - 최초 Home 실패 Fixture의 Exit 23 보존, Evidence Log 생성, 후속 진행 차단과 exact Daon Predicate 계약.
- Progress와 본 Attempt 21 보고서.
- 미변경: `App.tsx`, Native/Bridge/Xcode Project/UI Test, Evidence Writer/Workflow, Android, C19 Process 순서, Warm Deep Link·Rejected Link·Permission·Lifecycle·최종 Log/종료, Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C20 RED | iOS 35/37 PASS·2 FAIL: Adapter 원값 반환과 최초 Home 단순 Fail-fast 재현 |
| C20 GREEN | iOS 37/37 PASS |
| Restore Matrix | `Notifications` 문자열 보존, `null`·`undefined`·비문자열은 `null` PASS |
| 실패 Evidence Fixture | Home Wait Exit 23 유지, `initial-home-failure.log` 생성, 후속 실행 0 PASS |
| 성공·기존 시나리오 | Install→Terminate→Clear→Launch→Home, Warm Route 7종, Rejected/Permission/Lifecycle, 최종 Log·Crash/Secret·종료 계약 유지 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 37/37, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 300/300 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; 허용 파일 외 Product/Native/Bridge/Project/UI Test/Evidence/Workflow/Android/Lock/Pin Diff 0; 개인 절대경로 0; Signing 0; Pods/Build/Artifact/Test Temp 잔존 0 |

### 오류·복구 근거

- RED 35/37은 승인 C20의 Adapter Runtime 경계와 시작 실패 Evidence 공백을 재현한 결과이며 나머지 iOS/Evidence/Deep Link 계약 35개는 모두 통과했다.
- 구현·Portable 회귀 중 예상하지 못한 오류는 없었다.
- Windows에서는 실제 Swift nil→JS Runtime 값과 macOS Unified Log Artifact를 검증할 수 없으므로 macOS 성공을 주장하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행해 UI Test 3개와 후속 Simulator Verification 전체를 판정한다.
3. 실패 시 `initial-home-failure.log`, 성공 시 Home 준비·Warm Route 7종·Rejected/Permission/Lifecycle·최종 Log/종료 Artifact를 확인한다.
4. 성공 Manifest와 Artifact 확인 전에는 iOS Phase A 완료로 판정할 수 없다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

COMPLETED | R1-M3-06-I001 | C01 Fail-close Evidence·Production 권한 실제 UI 검증 보정 | 필수 Outcome Manifest·권한 3단계 XCTest Artifact·계약 Test·Architecture·Evidence·Progress 변경 | Targeted 17/17·Mobile 전체·Node 279/279·Toolchain·Independence PASS | 공통 Gate·macOS exact-SHA CI·Signing/실기기 미수행 | 어울1의 장시간 공통 Gate와 macOS CI Artifact 판정

# R1-M3-06 Attempt 2 결과보고

## 판정

C01 수정 개발 패킷은 `COMPLETED`이며 현재 전체 상태는 `IMPLEMENTED_PENDING_MAIN_GATE`다. 선행 Step 실패에도 성공 Manifest가 생성될 수 있던 결함과, `simctl privacy`만 바꾸고 Production 권한 요청 결과를 검증하지 않던 누락을 승인 범위 안에서 보정했다. Windows에서는 iOS Native Build·XCTest·Simulator 성공을 주장하지 않는다. 공통 Gate와 macOS exact-SHA CI는 어울1 후속이고 Apple Signing·실기기는 Phase B다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- Workflow의 Checkout·Node Setup·Xcode·npm·CocoaPods·Lock Install·Portable Contract·Pods·Simulator·Build·일반 UI Test·Simulator Verification 필수 12 Step에 안정 ID와 Outcome을 부여했다.
- Manifest는 모든 필수 Outcome이 `success`이고 exact SHA·Runner·Toolchain·Simulator 식별자, 승인 `phase-a-status.txt`, 필수 Source와 일반·권한 XCTest Result Bundle 4개가 모두 존재할 때만 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`와 `verification_completed:true`를 기록한다.
- 필수 Step `failure`는 `FAILED`와 실패 Step 목록, 누락·`skipped`·`cancelled`·`unknown`·Artifact 누락은 `INCOMPLETE`와 `verification_completed:false`를 기록한다. Checkout 실패로 Repository Script가 없어도 Workflow Fallback은 실패 Outcome을 읽어 성공 상태를 기록하지 않는다.
- Camera·Microphone·Notification 각각은 Simulator Privacy를 `grant → revoke → grant`로 설정한 뒤, 각 단계의 XCTest가 공용 Production UI 버튼을 실제 탭해 Native `requestPermission` 결과 `GRANTED → DENIED → GRANTED`를 확인한다.
- 일반 Route·Lifecycle·실제 Settings App 전환 Test와 권한 전용 Test 3회를 명시적으로 분리했다. Test Skip이나 조건부 성공 없이 각 명령 실패가 필수 Step 실패로 전파된다.
- 권한 단계마다 앱을 종료하고 XCTest가 다시 실행·종료하며, `DaonUITests.xcresult`와 `permission-grant-initial/revoke/grant-again.xcresult`를 exact Commit SHA Artifact에 포함한다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: 필수 Step ID·Outcome, Fail-close Manifest 입력, 일반/권한 XCTest Artifact 분리.
- `apps/mobile/ios/ci/write-evidence.mjs`: 성공·실패·불완전 Fixture 판정과 Source/XCTest Bundle 존재 검증.
- `apps/mobile/ios/ci/verify-simulator.sh`: 권한 3단계별 실제 Production 버튼 XCTest 실행.
- `apps/mobile/ios/DaonUITests/DaonUITests.swift`: 권한 결과 UI 검증과 Settings 버튼 안정성.
- `apps/mobile/src/MobileShell.tsx`: 권한 결과와 화면 내용의 안정 접근성 Label.
- `scripts/tests/ios-native-shell.test.mjs`, `scripts/tests/ios-phase-a-evidence.test.mjs`: Workflow·권한·성공/실패/불완전 Fixture 계약.
- iOS Architecture Contract, R1-M3-06 Local Evidence, Progress와 본 Attempt 2 보고서를 갱신했다.
- Android Native Production, Web·Studio·API·Desktop Production, 공개 API·데이터·보안·Signing 계약은 변경하지 않았다.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C01 RED | 10/15 PASS·5 FAIL: 필수 Step ID, 권한 실제 결과, Manifest 성공/실패/불완전 Fixture 결함 재현 |
| C01 최종 Targeted | iOS·Parser·Evidence 17/17 PASS |
| Evidence Fixture | 성공·필수 Step 실패·Outcome/식별자/상태 누락 3/3 PASS |
| Workflow/Syntax | JSON Parse PASS, Embedded Node Program Syntax PASS, Workflow Bash 8/8·Shell File 2/2 PASS |
| Mobile Lint·Type | 14 files, Exit 0 |
| Mobile Unit·Studio Contract | 9/9, 15/15 PASS |
| Android·iOS Native Contract | 11/11, 17/17 PASS |
| Android·iOS Production Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node Test | 279/279 PASS |
| Toolchain | 7 npm manifests exact pins PASS |
| Independence | components 8, edges 10, package files 10, scanned files 125, violations 0 |
| Production Audit | C01 의존성·Lock 변경 0. Attempt 1의 High/Critical 0·공개 Fix 없는 RN CLI 전이 Moderate 10 근거 유지 |
| 공통 Quality Gate | 어울1 지시에 따라 재실행하지 않음 |
| iOS Native Build·XCTest·Simulator | Windows에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- RED Test Patch의 큰 문맥 2회 불일치는 원자적으로 미적용됐다. 기존 Source를 손상하지 않고 작은 Patch로 분리해 RED 5건을 재현했다.
- 1차 GREEN은 14/15였다. 남은 1건은 기존 정적 Test가 하드코딩된 `privacy grant <service>` 순서만 인식해 공통 함수의 `privacy <action> <service>`를 오인한 것이며, 세 서비스·세 단계·XCTest 호출을 직접 확인하는 동등 계약으로 보정했다.
- PowerShell PATH의 `bash` 부재로 Shell Syntax 2건이 미실행됐으나 설치된 Git for Windows Bash를 확인해 동일 `-n` 검증과 Workflow 내 Bash 8개 검증을 완료했다.
- Independence 첫 실행은 기존 Evidence 파일 Write의 Sandbox `EPERM`으로 중단됐다. 승인된 동일 명령을 권한 환경에서 한 번 실행해 violations 0을 확인하고 자동 생성 Evidence 2개만 HEAD로 복원했다.
- C01 Fixture를 Root `verify:ios-native` Gate에 포함한 뒤 첫 Mobile 재실행은 이전 Gate 문자열을 고정한 정적 Test 1건 때문에 16/17에서 중단됐다. 승인된 Gate 확장 문자열로 기대값을 갱신한 뒤 전체 Mobile과 iOS Gate 17/17을 재PASS했다.

### 미해결 사항과 다음 판단

1. 어울1이 어울2 쓰기 종료 후 `npm run verify:quality-gate`를 승인 권한·긴 Timeout으로 재실행해야 한다.
2. 공통 Gate 통과·Commit·Push 뒤 exact SHA의 GitHub macOS Workflow를 실행하고 `workflow-outcomes.json`, `evidence-manifest.json`, 일반 XCTest 1개와 권한 단계별 XCTest 3개를 검토해야 한다.
3. macOS CI 성공 전에는 iOS Native Build·권한·Simulator 완료로 판정할 수 없다.
4. Apple Developer Team·Certificate·Provisioning Profile·서명 Archive·실기기 검증은 별도 승인 Phase B다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

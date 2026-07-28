COMPLETED | R1-M3-06-I007 | C19 Simulator 초기 Process 경계 고정 | Install 뒤 exact Daon Process 종료 1행과 순서·대상·최종 Cleanup 구분 계약·Progress·Attempt 20 변경 | iOS 35/35·Mobile 전체·Android 11/11·Node 298/298·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA macOS Simulator 전체 검증 미실행 | 어울1의 Commit·Push와 macOS CI·Artifact 판정

# R1-M3-06 Attempt 20 결과보고

## 판정

C19 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. UI Test가 남긴 Background Daon Process와 직접 수정한 UserDefaults Plist 사이의 경계를 설치 직후 exact Bundle Process 종료로 고정했다. C18 App·Route 초기화·준비 신호와 이후 검증은 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `c736fb163f34f1116c019d7a808a610ce75fba82`의 Run `30244183306`에서 공통 Gate·Pods·Simulator·Build와 UI Test 3개가 성공했다.
- 마지막 Settings UI Test는 Daon을 Background Process로 남길 수 있으나 후속 Script는 App Install 뒤 기존 Process 종료 없이 Container Plist의 `native_route_key`를 제거하고 `simctl launch`했다.
- 기존 Process가 재활성화되면 UserDefaults Cache와 Plist 수정의 일관성이 보장되지 않고 C18의 Restore·Listener 초기화 Effect도 다시 실행되지 않는다.
- 실제 `wait_for_route Home`은 기존 20회 동안 값이 빈 상태로 만료돼 새 Process 초기화가 수행되지 않은 경계와 일치했다.

## 조치

### 변경 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
  - App Install 직후 `xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}" >/dev/null 2>&1 || true` 1행 추가.
  - 실행 중이 아닌 경우만 명시적으로 허용하면서 exact Simulator·`com.sinsan.daon` Bundle만 대상으로 고정.
  - 순서를 Install→기존 Process 종료→`native_route_key` 제거→새 Process Launch→Home 준비로 고정.
- `scripts/tests/ios-native-shell.test.mjs`: 초기 종료의 exact 대상·허용 처리·순서, 최종 Cleanup Terminate와 분리, Shutdown/Erase/Uninstall/다른 Process 종료 금지 계약 추가.
- Progress와 본 Attempt 20 보고서.
- 미변경: Product Source, Native/Bridge/Xcode Project/UI Test, Evidence/Workflow, Android, C18 App Null Restore·Home 저장, Warm Deep Link 7종, Rejected Link·Permission·Lifecycle·Crash/종료, Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C19 RED | iOS 34/35 PASS·1 FAIL: Install 뒤 초기 exact Bundle Terminate 부재 재현 |
| C19 GREEN | iOS 35/35 PASS |
| Process 경계 | Install→초기 exact Bundle Terminate→Route Key Clear→Launch→Home 순서와 후반 최종 Cleanup 분리 PASS |
| 대상·완화 금지 | exact Simulator·Bundle 외 종료, Shutdown·Erase·Uninstall·Kill 0; 고정 Sleep·Wait 증가 0 |
| 기존 시나리오 | C18 Null Restore·Route 8종·Rejected Link 5종·Permission 3단계·Lifecycle·Crash/Secret·종료 계약 유지 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 35/35, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,145 bytes SHA-256 `5391D213589D09F8FFA91B6F76B878972D02DA3BF8663FDC462FD87706E8DE52` |
| 전체 Node | 298/298 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Product/Native/Bridge/Project/UI Test/Evidence/Workflow/Android/Lock/Pin Diff 0; 개인 절대경로 신규 0; Signing 0; Pods/Build/Artifact/Test Temp 잔존 0 |

### 오류·복구 근거

- RED 34/35는 승인 C19가 지정한 초기 Process 종료 누락을 재현한 결과이며 나머지 iOS/Evidence/Deep Link 계약 34개는 모두 통과했다.
- 구현·Portable 회귀 중 예상하지 못한 오류는 없었다.
- Windows에서는 실제 Background Process 종료·UserDefaults Cache 초기화와 Simulator 전체 흐름을 검증할 수 없으므로 macOS 성공을 주장하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행해 UI Test 3개와 후속 Simulator Verification 전체를 판정한다.
3. Artifact에서 초기 Daon 종료 뒤 Home 준비, Warm Route 7종, Rejected Link·Permission·Lifecycle·Crash/Secret·최종 종료 성공을 확인한다.
4. 성공 Manifest와 Artifact 확인 전에는 iOS Phase A 완료로 판정할 수 없다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

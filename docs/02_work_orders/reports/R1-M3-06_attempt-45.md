COMPLETED | R1-M3-06-I007 | iOS 26 Simulator Settings Search fallback 구현 | XCTest·Simulator Script·계약 Test·Progress·Attempt 45 | RED 50/51→GREEN 52/52·Mobile·Android 11/11·Node 316/316·Toolchain·Workflow/Bash·Bundle·Diff PASS | 실제 macOS iOS 26 Search 접근성·OFF/DENIED→ON/GRANTED Runtime·최종 Artifact 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 판정

# R1-M3-06 Attempt 45 결과보고

## 판정

C44 승인 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 기존 실기기 우선 경로를 보존하고, iOS 26 Simulator에서만 Settings Search fallback을 허용해 구현·Portable 회귀 검증을 완료했다. 정식 `FAILURE_REPORT`는 0회이며 TP Wave에는 도달하지 않았다.

## 판단 이유

- C43 exact-SHA `b1caddef190e9a5c0f9dc093fc357e976da0c41f`, Run `30313465208`, Job `90133918323`은 Build·일반 UI Test 성공과 `DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=OPENED AUTH=GRANTED`를 함께 증명했지만 Settings가 global 화면에 남았다.
- 이 증거는 Product Bridge와 권한 상태가 정상이고, 실패 원인이 iOS 26 GitHub-hosted Simulator의 resource navigation 한계임을 가리킨다.
- 따라서 Product/Host/Bridge/API를 변경하지 않고 XCTest의 direct Switch→exact Notifications row가 모두 부재한 경우에만 iOS 26 exact Search fallback을 적용했다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - Search button/field/result/app surface 4개 Stage를 추가했다.
  - 최종 알림 행 0건만 별도 오류로 구분하고 iOS 26에서만 exact Search button/field와 exact `Daon` 결과를 사용한다.
  - Search 결과 진입 후 direct exact Allow Notifications Switch를 우선하고, 없으면 기존 exact Notifications row→Switch 순서를 재사용한다.
  - 다건·비 Hittable·App Surface 미확인은 고정 Assertion으로 Fail-close한다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - Search 4개 Stage와 Assertion Code를 allowlist·Parser에 추가하고 기존 원 Error와 Exit 65를 보존한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - iOS 26 exact Search·우선순위·금지 패턴과 Bash 4 Assertion/4 Stage의 실제 Exit 65 분류 계약을 추가했다.
- C44 작업지시·Prompt·Progress와 본 Attempt 45 보고서.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C44 RED | iOS 계약 50/51 PASS·1 FAIL: iOS 26 Search helper 부재에서 예상 실패 |
| C44 GREEN | 구현 후 51/51, Bash Runtime Fixture 보강 후 `verify:ios-native` 52/52 PASS |
| Mobile 전체 | Lint 14 files·Type·Unit 10/10·Contract 15/15·Android 11/11·iOS 52/52 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 316/316 PASS |
| Toolchain·Workflow | Toolchain 7 Manifest·exact Pin·Lockfile, Workflow YAML 2/2 PASS |
| Bash·Syntax | iOS CI Bash 3/3 `bash -n`, Node Test Syntax PASS |
| 경계 | `git diff --check` PASS; Product Host·Bridge·Android·Workflow·Package/Lock·Xcode Project Diff 0 |

## 기존 동작·안전 경계 보존

- 기존 direct exact Switch와 exact Notifications row가 Search보다 항상 우선한다.
- Search는 iOS 26 이상이며 최종 행 0건인 테스트 경로에서만 실행한다. iOS 26 미만은 기존 Fail-close를 유지한다.
- 좌표·Index·`firstMatch`·부분/정규식 Label·private URL·TCC/Settings DB 조작·재설치·무제한 대기 또는 Scroll을 추가하지 않았다.
- Product Host·Bridge 8 Selector·공개 API·권한 의미·Workflow·Dependency·Lockfile·Project·Android 변경은 없다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인한 후 Commit·Push한다.
2. 새 exact SHA macOS iOS 26 Workflow에서 Search button/field/result/app surface와 Permission OFF/DENIED→ON/GRANTED Runtime을 판정한다.
3. 실제 macOS Simulator Search 접근성과 최종 Artifact는 Windows Portable 검증으로 대체하지 않았다.
4. Commit·Push·PR·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.

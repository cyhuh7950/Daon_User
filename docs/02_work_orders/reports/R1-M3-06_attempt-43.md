COMPLETED | R1-M3-06-I007 | 기존 generic 보존·iOS 알림 전용 공식 설정 진입·iOS 15.1 fallback 구현 | Mobile Shell·iOS Host/Bridge·Permission XCTest·계약 Test·Progress·Attempt 43 | RED 37/41→GREEN 41/41·iOS 48/48·Mobile·Android 11/11·Node 312/312·Toolchain·Workflow/Bash·Bundle·Diff PASS | 실제 macOS iOS16/15.1 Settings Runtime·최종 Artifact 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 판정

# R1-M3-06 Attempt 43 결과보고

## 판정

C42 승인 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 기존 `앱 권한 설정` 버튼과 generic `openApplicationSettings()` 의미를 그대로 보존하고, iOS Adapter에만 선택적 `openNotificationSettings()`를 추가했다. iOS 16 이상은 Apple 공개 알림 설정 URL을 사용하고 최소 지원 iOS 15.1은 기존 generic 설정 URL로 fallback한다. failure count는 0이다.

## 판단 이유

- Head `92e1f6a8a24022fe4854fd074085946a53f1f15b`의 iOS Run `30292083384`, Job `90064002676`에서 기존 generic 설정 진입 후 global Settings의 Siri·Camera·Home Screen·Search 등이 노출되어 Daon 알림 설정 화면 미진입이 확인됐다.
- 기존 generic 진입을 알림 전용으로 바꾸면 카메라·마이크 설정 UX가 회귀하므로, 신산님 승인대로 기존 동작을 보존한 별도 선택적 iOS 진입이 필요했다.
- iOS 16 이상의 공개 `UIApplication.openNotificationSettingsURLString`과 iOS 15.1의 `UIApplication.openSettingsURLString` fallback은 private Scheme·TCC 조작 없이 지원 범위를 충족한다.

## 조치

### 변경 범위

- `apps/mobile/src/MobileShell.tsx`
  - `NativePermissionAdapter`에 선택적 `openNotificationSettings?()`를 추가했다.
  - 메서드가 실제 존재할 때만 `알림 설정` 버튼과 `알림 설정 열기` 접근성 Label을 표시한다.
  - 기존 3개 권한 요청 버튼과 `앱 권한 설정` 버튼은 변경하지 않았다.
- `apps/mobile/src/platform/ios-host.ts`
  - iOS Native Module 타입·호출 함수·iOS Adapter에 알림 설정 진입을 연결했다.
- `apps/mobile/ios/Daon/DaonIOSHostBridge.m`
  - `openNotificationSettings` Selector 한 개를 추가해 승인 Bridge를 정확히 8개로 유지했다.
- `apps/mobile/ios/Daon/DaonIOSHost.swift`
  - iOS 16 이상 공개 알림 설정 URL, iOS 15.1 generic fallback을 선택한다.
  - URL 생성 실패는 고정 Code `IOS_NOTIFICATION_SETTINGS_URL_UNAVAILABLE`로 reject한다.
  - `UIApplication.shared.open`의 Bool 결과 resolve 의미는 기존과 동일하다.
- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - revoke·grant-again이 새 Production `알림 설정 열기` 버튼을 탭한다.
  - Settings foreground 뒤 exact `Allow Notifications`/`알림 허용` Hittable Switch를 먼저 단일성 Fail-close로 찾는다.
  - 직접 Switch가 없을 때만 iOS 15.1용 기존 exact Notifications 행 Helper를 사용한다.
  - 기존 C41 접근성 요약은 fallback의 최종 행 0건 분기에만 남아 전용 화면 성공 경로에서는 실행되지 않는다.
- `scripts/tests/ios-native-shell.test.mjs`
  - Bridge 8개, 공개 URL·fallback, 선택적 버튼·generic 보존, direct Switch 우선·행 fallback, 3 Phase와 금지 경계를 고정했다.
- Progress와 본 Attempt 43 보고서.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C42 RED | iOS 계약 37/41 PASS·4 FAIL: Bridge·URL·UI·XCTest 신규 계약 부재에서 예상 실패 |
| 첫 GREEN | 40/41 PASS·1 FAIL: Production 계약은 충족했고 Test가 optional chaining 동등 표현을 직접 호출로만 요구 |
| C42 GREEN | Test 표현을 동등 계약으로 정합화한 뒤 iOS 계약 41/41 PASS |
| iOS aggregate | Native Shell·Deep Link·Evidence 48/48 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 48/48 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 312/312 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Node YAML Parser 2/2, Git Bash iOS Script 3/3, Node Test Syntax PASS |
| 경계 | `git diff --check` PASS; Android·Xcode Project·Product·Workflow·Package/Lock Diff 0; Bridge 8개; 금지 Pattern 0 |

### 기존 동작·안전 경계 보존 근거

- 기존 `openApplicationSettings()` Swift·TypeScript 호출과 `앱 권한 설정` 버튼은 삭제·변경하지 않았다.
- Android Adapter·Source에는 새 메서드나 동작 변경이 없으며 선택적 UI 계약으로 Android에는 알림 설정 버튼이 나타나지 않는다.
- private URL Scheme, TCC/Settings DB 조작, 알림 simctl 권한 조작, 재설치, 좌표·Index·`firstMatch`·부분문자열 Selector·무제한 반복을 추가하지 않았다.
- Settings 앱 foreground만으로 성공 처리하지 않고 direct Switch 또는 기존 exact Notifications 행을 반드시 통과한다.
- Dependency·Lockfile·Workflow·Bundle ID·Deployment Target·Signing 변경은 없다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA macOS iOS Workflow에서 iOS 16 이상 알림 전용 화면과 동일 설치 grant-initial → OFF/DENIED → ON/GRANTED를 실제 UI·상태로 확인한다.
3. 별도 iOS 15.1 Runtime에서 generic fallback 뒤 기존 exact Notifications 행 진입과 Switch OFF/ON을 확인한다.
4. 실제 macOS Simulator Runtime·최종 Artifact는 본 Windows 로컬 환경에서 검증하지 않았다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.

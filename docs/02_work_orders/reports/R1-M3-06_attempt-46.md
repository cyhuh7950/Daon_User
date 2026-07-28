COMPLETED | R1-M3-06-I007 | recoverable COMPOSITE_ZERO 선행 XCTFail 제거와 Fail-close 경계 보존 | XCTest·iOS 계약 Test·Progress·Attempt 46 | RED 52/53→GREEN 53/53·Mobile·Android 11/11·Node 317/317·Toolchain·Workflow/Bash·Bundle·Diff PASS | 실제 macOS iOS 26 Search·OFF/DENIED→ON/GRANTED Runtime·최종 Artifact 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 판정

# R1-M3-06 Attempt 46 결과보고

## 판정

C45 승인 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. recoverable COMPOSITE_ZERO 두 호출에서만 선행 XCTest failure 없이 typed sentinel을 전달하고, iOS 26 미만과 다른 모든 경계의 즉시 Fail-close를 유지했다. 정식 `FAILURE_REPORT`는 0회이며 TP Wave에는 도달하지 않았다.

## 판단 이유

- exact-SHA `507e7825e2428ad7cc18c43f1ec1e4da3361d420`, Run `30316280643`, Job `90142395318`은 Build·일반 UI 성공, Search button `hittable=1`, Marker `OPENED AUTH=GRANTED`를 증명했다.
- Permission revoke의 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO / 65`는 line 341의 `XCTFail`이 sentinel보다 먼저 기록되어 Search 성공 여부와 무관하게 XCTest failure가 남는 제어 흐름 결함이었다.
- Product/Bridge/권한 경로가 정상이라는 증거에 따라 XCTest helper와 계약 Test만 최소 수정했다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `requireExactNotificationSettingsRow`에 기본값 `allowAbsent=false`를 추가했다.
  - 최종 COMPOSITE_ZERO에서 `allowAbsent=true`인 두 호출만 Accessibility Summary 후 선행 `XCTFail` 없이 typed sentinel을 throw한다.
  - initial row→iOS 26 Search와 Search 결과 app surface probe에만 `allowAbsent=true`를 전달한다.
  - iOS 26 미만 catch는 기존 COMPOSITE_ZERO Assertion을 명시적으로 발생시킨다.
  - direct/semantic/label/composite ambiguity와 non-hittable 경계는 변경하지 않았다.
- `scripts/tests/ios-native-shell.test.mjs`
  - recoverable 두 호출 한정, 기본 Fail-close, pre-iOS26 COMPOSITE_ZERO, Search app surface 단일 Assertion을 고정했다.
  - C45 승인 의미와 충돌한 기존 무인자 호출·연접 Assertion 표현 2건만 동등 의미로 정합화했다.
- C45 작업지시·Prompt·Progress와 본 Attempt 46 보고서.
- `apps/mobile/ios/ci/verify-simulator.sh`는 변경하지 않았다.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C45 RED | iOS 계약 52/53 PASS·1 FAIL: 신규 recoverable helper 계약 부재에서 예상 실패 |
| 첫 GREEN | 신규 C45 계약 PASS, 기존 정적 표현 2건만 불일치하여 51/53 |
| C45 GREEN | 기존 Test를 승인된 동등 의미로 정합화 후 `verify:ios-native` 53/53 PASS |
| Mobile 전체 | Lint 14 files·Type·Unit 10/10·Contract 15/15·Android 11/11·iOS 53/53 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 317/317 PASS |
| Toolchain·Workflow | Toolchain 7 Manifest·exact Pin·Lockfile, Workflow YAML 2/2 PASS |
| Bash·Syntax | iOS CI Bash 3/3 `bash -n`, Node Test Syntax PASS |
| 경계 | `git diff --check` PASS; Product Host·Bridge·Android·Workflow·Package/Lock·Xcode Project·Simulator Script Diff 0 |

## 기존 동작·안전 경계 보존

- silent absence는 정확히 두 `allowAbsent=true` 호출에만 존재하고 helper 기본 동작은 Fail-close다.
- COMPOSITE_AMBIGUOUS, direct/semantic ambiguity, label non-hittable과 switch/result ambiguity는 기존 Assertion과 오류를 유지한다.
- Search app surface 0건은 선행 COMPOSITE_ZERO failure 없이 `Daon notification settings surface` Assertion 하나로 분류된다.
- 제품 코드·공식 URL·권한 의미·Bash Code/Stage·Workflow·Dependency·Lockfile·Project·Android 변경은 없다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인한 후 Commit·Push한다.
2. 새 exact SHA macOS iOS 26 Workflow에서 revoke·grant-again의 Search와 최종 OFF/DENIED→ON/GRANTED Runtime을 판정한다.
3. 실제 macOS Runtime과 최종 Artifact는 Windows Portable 검증으로 대체하지 않았다.
4. Commit·Push·PR·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.

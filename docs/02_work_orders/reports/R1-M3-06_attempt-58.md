COMPLETED | R1-M3-06-I007 | fresh exact Notification switch의 실제 control 영역을 element-relative normalized coordinate로 조건부 1회 탭 | Swift XCTest·정적 계약·C57 문서·Progress·Attempt58 | RED0/1→iOS68/68·Mobile·Node332/332·Toolchain/YAML/Bash/Bundle/Diff PASS | 실제 macOS exact-SHA Runtime·최종 Artifact 미확인 | 단일 Commit·Push 후 어울1 CI 판정

# R1-M3-06 Attempt 58 결과보고

## 판정

C57 승인 수정은 `COMPLETED`이며 상태는 `SWITCH_CONTROL_POINT_PENDING_MACOS_CI`다. C56의 fresh exact element와 before/after/final Marker 계약을 유지하면서, 상태 변경이 필요할 때만 해당 element 우측 중앙 `CGVector(dx: 0.9, dy: 0.5)`를 정확히 한 번 탭하도록 최소 교체했다. 정식 `FAILURE_REPORT`와 C57 `INCOMPLETE`는 0회이며 TP Wave에는 도달하지 않았다.

## 판단 이유

- exact-SHA Run `30369438084`, Job `90309102055`에서 revoke의 before와 일반 `tap()` 후 after가 모두 on이었고, identifier·count·label은 정상이라 stale element가 아닌 기본 hittable point의 비토글 영역 선택으로 원인이 좁혀졌다.
- 전용 helper는 fresh exact `XCUIElement`에만 종속된 normalized coordinate를 사용하며 frame·screen·application 절대 좌표, index/prefix/contains selector, retry·재탭을 추가하지 않는다.
- before가 target과 같으면 탭하지 않고, 다를 때만 helper를 한 번 호출한다. 기존 before/after/final Marker, 5초 wait, final assert, fail-close와 Exit 65는 보존했다.
- 전체 iOS 첫 검증에서 실패한 2건은 승인 coordinate까지 차단한 과거 전역 금지 테스트였다. exact 승인 문자열 한 건만 검사본에서 제외해 다른 coordinate와 모든 기존 우회 금지는 계속 차단했다.

## 변경 결과

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `tapTarget.tap()`을 `tapNotificationSwitchControl(tapTarget)`로 교체했다.
  - exact element의 `(0.9, 0.5)` normalized point를 한 번 탭하는 전용 helper를 추가했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - C57 RED/GREEN 계약과 C56 승계 계약을 고정했다.
  - 두 기존 전역 검사에서 exact 승인 coordinate 한 줄만 예외 처리하고 나머지 금지식을 유지했다.
- C57 작업지시서·프롬프트·Progress·본 Attempt58 보고서를 기록했다.

## 테스트 결과

| 검증 | 결과 |
|---|---|
| C57 TDD | RED 0/1, 구현 후 C54~C57 4/4 PASS |
| iOS Native | 68/68 PASS |
| Mobile | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 68/68, Bundle PASS |
| 전체 Node | 332/332 PASS |
| Toolchain | 7 npm manifests·exact pins·lockfiles PASS |
| Workflow·Syntax | YAML 2/2, Git Bash 3/3, Node syntax, `git diff --check` PASS |
| Bundle | Android 927506 bytes `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921716 bytes `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |

`verify:mobile`은 기존 `services/local-service/.pytest_cache` EPERM 읽기 경고를 출력했으나 Exit 0이었고 C57 관련 변경은 없다.

## 조치

1. 허용된 6개 파일만 단일 목적 Commit으로 묶어 `codex/r1-m3-06`에 Push한다.
2. 어울1은 exact SHA의 macOS CI에서 revoke·grant-again 실제 switch Runtime과 최종 Artifact를 판정한다.
3. Windows Portable 검증은 실제 macOS Simulator 증거를 대체하지 않는다.

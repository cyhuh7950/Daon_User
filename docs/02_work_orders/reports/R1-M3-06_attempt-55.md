COMPLETED | R1-M3-06-I007 | C54 Apps-local Settings 검색 경로 | Swift XCTest·정적 계약·C54 문서·Progress·Attempt55 | iOS65/65·Mobile·Node329/329·Toolchain/YAML/Bash/Bundle/Diff PASS | 실제 macOS exact-SHA E2E 미확인 | 어울1 Commit/Push 후 CI 판정

# R1-M3-06 Attempt 55 결과보고

## 판정

- `COMPLETED`. 기준 HEAD `f6dffdb68df234bedd37c0d63830984936e8faf1`.
- 동일 issue의 정식 `FAILURE_REPORT` 0회, C54 유효 `INCOMPLETE` 1회, TP Wave 미도달.

## 판단 이유

Run `30359006728`·Job `90273850437`의 증거에 따라 iOS 26 Settings 전역 검색 경로를 폐기하고, Settings 메인의 exact Apps 버튼(`com.apple.settings.apps`)을 최대 8회 bounded `swipeUp`으로 찾은 뒤 Apps-local SearchField를 우선 평가하도록 교체했다. SearchField 0건일 때만 최대 6회 `swipeDown`하며, 각 exact 요소는 2건 이상 또는 최종 0건에서 즉시 Fail-close한다. C52/C53 keyboard Continue 처리, exact Daon 결과 선택, C51 진단, Stage·Assertion·Exit 65 계약은 보존했다.

## 생성·변경 결과

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`: 승인된 Apps-local fallback 내부만 변경.
- `scripts/tests/ios-native-shell.test.mjs`: C54 exact/bounded/금지 패턴 계약 추가, 폐기된 C50 전역 검색 전제를 Apps-local 계약으로 정합.
- C54 작업지시서·프롬프트, Progress와 본 Attempt 보고서를 생성·갱신.

## 검증

- C54 핵심 계약: 3/3 PASS.
- iOS Native: 65/65 PASS.
- Mobile: Lint 14, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 65/65, Bundle PASS.
- 전체 Node: 329/329 PASS.
- Toolchain: 7개 Manifest exact pin·lockfile PASS.
- Workflow YAML/JSON 2/2, iOS CI Bash 3/3, Node syntax, `git diff --check` PASS.
- Bundle은 기존과 동일: Android 927506 bytes / `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921716 bytes / `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616`.
- Product·Android·Workflow·dependency/lock/project 변경 0건.

## 미해결 및 조치

- Windows portable 검증은 실제 macOS iOS 26 Simulator Settings 동작을 대체하지 않는다.
- 어울1이 Diff를 인수해 Commit·Push하고 exact-SHA macOS CI에서 Apps-local Daon 결과·알림 OFF/ON E2E와 최종 Artifact를 판정한다.

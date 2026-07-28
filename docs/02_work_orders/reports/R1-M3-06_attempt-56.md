COMPLETED | R1-M3-06-I007 | C55 exact Bundle ID app result 우선 선택 | Swift XCTest·정적 계약·C55 문서·Progress·Attempt56 | iOS66/66·Mobile·Node330/330·Toolchain/YAML/Bash/Bundle/Diff PASS | 실제 macOS exact-SHA E2E 미확인 | 어울1 Commit/Push 후 CI 판정

# R1-M3-06 Attempt 56 결과보고

## 판정

- `COMPLETED`. 기준 HEAD `1067fca3603ed7e5c8cdf2b6e5c52c23cdfd6749`.
- 동일 issue의 정식 `FAILURE_REPORT` 0회, C55 `INCOMPLETE` 0회, TP Wave 미도달.

## 판단 이유

Run `30363396550`·Job `90288408830`에서 Apps-local 검색 결과가 exact app button `identifier=com.sinsan.daon`과 내부 exact-label staticText를 함께 노출해 기존 descendant fallback이 2건으로 모호해지는 원인을 확인했다. 승인 공개 Application ID의 exact button query를 Wait와 선택의 최우선 경로로 추가했다. 버튼 1건은 즉시 선택하고, 2건 이상은 `Settings Daon app result`로 즉시 Fail-close하며, 0건일 때만 기존 exact-label cell·descendant fallback을 수행한다.

## 생성·변경 결과

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`: Daon 결과 선택 블록에 exact Bundle button 우선순위만 추가.
- `scripts/tests/ios-native-shell.test.mjs`: Wait 우선, 1건 선택, 2+ fail-close, 0 legacy fallback, Runner·금지 selector 배제와 후속 tap 계약 추가.
- C55 작업지시서·프롬프트·Progress와 본 보고서 생성·갱신.

## 검증

- TDD: RED 0/1 → targeted GREEN 5/5.
- iOS Native 66/66 PASS.
- Mobile: Lint 14, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 66/66, Bundle PASS.
- 전체 Node 330/330, Toolchain 7, Workflow YAML/JSON 2/2, iOS CI Bash 3/3, Node syntax, `git diff --check` PASS.
- Bundle 동일: Android 927506 bytes / `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921716 bytes / `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616`.
- Product·Simulator Script·Host/Bridge/API·Android·Workflow·dependency/lock/project/signing 보호 변경 0건.

## 미해결 및 조치

- Windows portable 검증은 실제 macOS iOS 26 Simulator Settings 동작을 대체하지 않는다.
- 어울1이 Diff를 인수해 Commit·Push하고 exact-SHA macOS CI에서 Daon app button tap 이후 알림 OFF/ON E2E와 최종 Artifact를 판정한다.

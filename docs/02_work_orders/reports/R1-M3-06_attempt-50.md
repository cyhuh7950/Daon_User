COMPLETED | R1-M3-06-I007 | Search button 탭 후 실제 접근성 표면을 별도 bounded Summary로 진단 | DaonUITests Surface Summary·Simulator strict Notice·C49 Test·정본/Prompt·Progress·Attempt 50 | RED 57/59→GREEN 59/59·Mobile·Node 323/323·Toolchain·Workflow YAML·Bash·Bundle·Diff PASS | 실제 macOS exact-SHA CI Surface Summary와 Simulator 후속 단계 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 진단 회수

# R1-M3-06 Attempt 50 결과보고

## 판정

- `COMPLETED`
- issue_id: `R1-M3-06-I007`
- 기준 HEAD: `b1bb0576edf3a973b09572cb76a35d9e36d97937`
- 정식 FAILURE_REPORT 누계: 0
- TP 테스트 시점: 미도달

## 판단 이유

- Run `30346370694` attempt 2, Job `90234247054`에서 exact Search button tap 이후 SearchField와 TextField가 모두 0건임이 확인됐다.
- 추측 선택자를 추가하지 않고 승인된 후보 유형의 실제 post-tap 접근성 표현을 별도 `DAON_SETTINGS_SEARCH_SURFACE_SUMMARY=v1`로 수집했다.
- 기존 Search 선택자·tap·input·Stage·Assertion·Exit 65와 C46/C48 Summary 계약은 유지됐다.

## 변경 결과

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - 후보를 `textView` → `other` → `button` → `staticText` 순서로 최대 24개 수집한다.
  - button 그룹은 exact identifier `com.apple.settings.search`를 먼저 수집한 뒤 나머지 button을 deterministic 순서로 수집한다.
  - label·identifier·value 중 하나가 비지 않았거나 hittable인 요소만 기존 sanitizer로 최대 48자 기록한다.
  - 기존 input Summary 직후 별도 Surface Summary를 failure guard에서 정확히 한 번 출력한다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - Bash 3.2 command substitution 방식으로 별도 Summary의 단일 행만 수집한다.
  - element type 4종, count 0~24, token 48, 전체 4096, schema·delimiter·injection·중복 행을 strict 검증한다.
  - 유효한 Summary만 Notice로 내보내고 원 Exit 65를 보존한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - Swift bounded 진단 계약과 Bash valid/empty/multiple/invalid/injection/oversize/count mismatch/Exit 65 Fixture를 추가했다.
  - C46 정적 계약은 C49 진단 호출을 허용하되 기존 input Summary가 먼저 실행됨을 유지하도록 정합했다.

## RED→GREEN 및 회귀 결과

| 검증 | 결과 |
| --- | --- |
| C49 RED | iOS 계약 57/59 PASS·신규 2개만 예상 실패 |
| 구현 후 | C49 신규 계약·Runtime PASS, 기존 C46 정적 표현만 1건 불일치 |
| C49 GREEN | C46 테스트 정합 후 iOS 59/59 PASS |
| Mobile 전체 | Lint 14 files·Type·Unit 10/10·Contract 15/15·Android 11/11·iOS 59/59 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 323/323 PASS |
| Toolchain·Workflow | Toolchain 7, Workflow YAML 2/2 PASS |
| Bash·Syntax | iOS CI Bash 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Product·Android·Workflow·Package/Lock·Xcode Project Diff 0 |

## 안전 경계

- Product Swift와 Search selector·tap·input·Stage·Assertion·Exit 65를 변경하지 않았다.
- debug/frame/pid/path/env/keyboard와 무제한 dump를 출력하지 않는다.
- Commit, Push, PR, GitHub, SSH, Server, GUI, Signing 작업을 수행하지 않았다.
- uv 최초 fetch 실패는 rerun으로 복구된 환경 중단이며 정식 실패보고에 포함하지 않았다.

## 조치

- 어울1이 Diff를 검토해 Commit·Push한다.
- 고정 SHA macOS CI에서 새 Surface Summary Notice를 회수해 실제 element type·label·identifier·value·hittable과 화면 전환 상태를 판정한다.

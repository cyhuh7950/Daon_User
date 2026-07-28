COMPLETED | R1-M3-06-I007 | 잘못된 Search and Look Up identifier 탭을 제거하고 공개 pull-down으로 SearchField를 bounded 탐색 | DaonUITests·C50 Test·정본/Prompt·Progress·Attempt 51 | RED 59/60→GREEN 60/60·Mobile·Node 324/324·Toolchain·Workflow YAML·Bash·Bundle·Diff PASS | 실제 macOS exact-SHA pull-down 동작·후속 Simulator 단계 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 판정

# R1-M3-06 Attempt 51 결과보고

## 판정

- `COMPLETED`
- issue_id: `R1-M3-06-I007`
- 기준 HEAD: `241e1eb693ed1df815df2df644565c8e01b97921`
- 정식 FAILURE_REPORT 누계: 0
- TP 테스트 시점: 미도달

## 판단 이유

- Run `30349204845`, Job `90242461826`의 Surface Summary로 `com.apple.settings.search`가 검색 입력 버튼이 아니라 Search and Look Up 설정 항목임을 확정했다.
- 잘못된 identifier query·대기·tap을 제거하고 Apple Settings의 공개 pull-down 제스처만 최대 6회 수행하도록 수정했다.
- 각 제스처 직후 기존 exact hittable SearchField query를 평가해 1건은 즉시 진행, 2건 이상은 즉시 Fail-close, 6회 뒤 0건은 기존 진단·`SETTINGS_SEARCH_FIELD_MISSING` 경로를 유지한다.

## 변경 결과

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `.settingsSearchButton` Stage는 기존 위치에 보존했다.
  - `com.apple.settings.search`, button query/wait/tap과 SearchField 고정 wait를 제거했다.
  - 최대 6회 `settings.swipeDown()` 루프와 매회 exact hittable SearchField 평가를 추가했다.
  - exact-one 조기중단, ambiguous 즉시 Fail-close, zero 기존 C46/C49 진단을 보존했다.
  - 검색어 `Daon`, exact 결과 선택, 알림 설정 Surface와 후속 Stage/Assertion은 변경하지 않았다.
- `scripts/tests/ios-native-shell.test.mjs`
  - 잘못된 identifier/tap 부재, bounded 6회, 매회 재평가, 조기중단, ambiguous/zero Fail-close와 후속 계약을 고정했다.
  - 과거 identifier를 요구하던 기존 iOS 26 계약을 승인된 pull-down 계약으로 정합했다.
- C50 작업지시서·Prompt·Progress와 본 Attempt 51 보고서를 생성했다.

## RED→GREEN 및 회귀 결과

| 검증 | 결과 |
| --- | --- |
| C50 RED | iOS 59/60 PASS·신규 C50 1건만 예상 실패 |
| 구현 후 | C50 계약 PASS, 과거 identifier 기대 Test 1건 표현 불일치 |
| C50 GREEN | 과거 Test 정합 후 iOS 60/60 PASS |
| Mobile 전체 | Lint14·Type·Unit10/10·Contract15/15·Android11/11·iOS60/60 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 324/324 PASS |
| Toolchain·Workflow | Toolchain7, Workflow YAML2/2 PASS |
| Bash·Syntax | iOS CI Bash3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Simulator Script·Product·Host/Bridge/API·Android·Workflow·Package/Lock·Project Diff 0 |

## 안전 경계

- 고정 sleep, coordinate/index, private API, predicate 확대와 설명 TextView/other/button 입력 우회를 추가하지 않았다.
- Simulator Script, Product, Android, Workflow, 의존성, Lockfile, Project와 Signing을 변경하지 않았다.
- Commit, Push, PR, GitHub, SSH, Server, GUI 작업을 수행하지 않았다.

## 조치

- 어울1이 Diff를 검토해 Commit·Push한다.
- 고정 SHA macOS CI에서 pull-down으로 SearchField가 정확히 1건 노출되는지와 Daon 결과·알림 설정 Surface 진입을 판정한다.

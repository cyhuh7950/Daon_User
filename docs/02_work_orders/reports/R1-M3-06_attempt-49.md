COMPLETED | R1-M3-06-I007 | macOS Bash 3.2 미지원 `mapfile` 수집을 단일 문자열·내부 개행 거부 방식으로 최소 교체 | Simulator Script·iOS 계약 Test·C48 정본/Prompt·Progress·Attempt 49 | RED 56/57→GREEN 57/57·Mobile·Node 321/321·Toolchain·Workflow YAML·Bash·Bundle·Diff PASS | 실제 macOS Bash 3.2 exact-SHA CI와 Simulator 후보 metadata·최종 Artifact 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 재판정

# R1-M3-06 Attempt 49 결과보고

## 판정

- `COMPLETED`
- issue_id: `R1-M3-06-I007`
- 기준 HEAD: `5957efcae973b3f709a9bd9e5ac6f16c0bb22006`
- 정식 FAILURE_REPORT 누계: 0
- 테스트 계획 Gate: 미도달

## 판단 이유

- exact-SHA Run `30345070183`, Job `90229262625`의 valid Summary Notice 0건 원인은 C46에서 도입한 macOS Bash 3.2 미지원 `mapfile`로 확정돼 있었다.
- 승인 범위 안에서 `report_settings_search_accessibility_notice`의 `mapfile`·`source_lines` 배열만 command substitution으로 교체했다.
- command substitution이 trailing newline을 제거하는 특성을 사용하되, 빈 결과를 거부하고 내부 `$'\n'`이 남으면 두 개 이상의 일치 행으로 판정해 Notice를 생략한다.
- `${source_line#*${prefix}}`, C47 token validator, 구조 ERE, count 0/max 16, 4096 byte, schema, Exit 65와 Swift Summary 계약은 보존했다.

## 변경 결과

- `apps/mobile/ios/ci/verify-simulator.sh`
  - `mapfile -t source_lines`와 배열 길이 판정을 제거했다.
  - `grep -F` command substitution, nonempty, 내부 newline 거부를 추가했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - C48 Bash 3.2 호환 수집 정적 계약을 추가했다.
  - 기존 C46 Runtime Fixture의 valid, `u{AC00}`, absent, 유효 2행 중복, invalid, injection, oversize 판정을 그대로 재실행했다.
- C48 작업지시서·실행 Prompt·Progress와 본 보고서를 생성했다.
- 전용 패치 도구의 C:\tmp deny-read ACL 오류는 승인 파일에 대한 exact 문자열 대체로 복구했으며, 대상 1건 검증 후 기록했다.

## RED→GREEN 및 회귀 결과

| 검증 | 결과 |
| --- | --- |
| C48 RED | iOS 계약 56/57 PASS·1 FAIL: 신규 계약이 기존 `mapfile/source_lines`를 정확히 검출 |
| C48 GREEN | `npm run verify:ios-native` 57/57 PASS |
| Mobile 전체 | Lint 14 files·Type·Unit 10/10·Contract 15/15·Android 11/11·iOS 57/57 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 321/321 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow YAML | 2/2 Parse PASS |
| Bash·Syntax | iOS CI Bash 3/3 `bash -n`, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Swift·Product·Android·Workflow·Package/Lock·Xcode Project Diff 0 |

## 안전 경계

- Swift, Product, Android, Workflow, 의존성, Lockfile, Xcode Project와 Signing을 변경하지 않았다.
- Commit, Push, PR, GitHub, SSH, Server, GUI 작업을 수행하지 않았다.
- 실제 macOS Bash 3.2와 Simulator 실행은 어울1이 고정 SHA를 Commit·Push한 뒤 CI에서 재확인해야 한다.

## 조치

- 어울1이 허용 파일 Diff를 인수해 Commit·Push한다.
- 동일 exact-SHA macOS CI에서 valid Summary Notice 1건과 후속 Simulator 단계 진입 여부를 판정한다.

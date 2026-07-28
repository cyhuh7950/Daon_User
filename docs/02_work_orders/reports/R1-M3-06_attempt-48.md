COMPLETED | R1-M3-06-I007 | Search Summary item token 검증을 macOS Bash 3.2 호환 방식으로 보정 | Simulator Script·iOS 계약 Test·Progress·Attempt 48 | RED 55/56→GREEN 56/56·Mobile·Android 11/11·Node 320/320·Toolchain·Workflow/Bash·Bundle·Diff PASS | 실제 macOS Bash 3.2 Gate·Simulator 후보 metadata·최종 Artifact 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 판정

# R1-M3-06 Attempt 48 결과보고

## 판정

C47 승인 이식성 수정 패킷은 `COMPLETED`이며 상태는 `PORTABILITY_FIX_PENDING_MACOS_CI`다. Swift Summary와 제품 동작을 변경하지 않고 Search Summary item의 단일 token ERE만 구조 ERE와 Bash 3.2 호환 length·ASCII validator로 분리했다. 정식 `FAILURE_REPORT`는 0회이며 TP Wave에는 도달하지 않았다.

## 판단 이유

- exact-SHA `b461f6cbacc592cae32ecd638311928ca120206c`, Run `30343791949`, Job `90225154593`은 Simulator 진입 전 Portable iOS contracts에서 54/55로 중단했다.
- valid Summary Fixture의 예상 Notice가 실제 `[]`였지만 Windows Git Bash에서는 55/55였다.
- 확인 원인은 단일 `([A-Za-z0-9_.+/{\}-]{1,48})` ERE의 macOS Bash 3.2 이식성이고 Swift·Product 진단 결함이 아니다.

## 조치

### 변경 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
  - item 구조 ERE는 elementType과 comma-delimited label·identifier·value·isHittable만 capture한다.
  - capture를 local 변수에 고정한 뒤 `settings_search_token_is_valid`에서 `${#token}` 1..48을 검사한다.
  - Bash `case`의 열거 ASCII allowlist로 letters·digits·`_ . + - / { }`만 허용한다.
  - count 0 `_none_`, 최대 16, 전체 4096, 단일 행, `::`·`%`, count/schema와 원 Exit 65 계약은 유지한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - Bash 3.2 호환 helper·구조/길이 분리·외부 도구 금지 계약을 고정했다.
  - valid `u{AC00}`와 comma·space·colon·49자 invalid Fixture를 보강했다.
- C47 작업지시·Prompt·Progress와 본 Attempt 48 보고서.
- Swift·Product·Android·Workflow는 변경하지 않았다.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C47 RED | iOS 계약 55/56 PASS·1 FAIL: 신규 Bash 3.2 validator 부재에서 예상 실패 |
| 첫 GREEN | Runtime Fixture PASS, static Test가 direct BASH_REMATCH 호출만 요구해 55/56 |
| C47 GREEN | capture를 local에 고정하는 안전 구현으로 Test 정합 후 `verify:ios-native` 56/56 PASS |
| Mobile 전체 | Lint 14 files·Type·Unit 10/10·Contract 15/15·Android 11/11·iOS 56/56 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 320/320 PASS |
| Toolchain·Workflow | Toolchain 7 Manifest·exact Pin·Lockfile, Workflow YAML 2/2 PASS |
| Bash·Syntax | iOS CI Bash 3/3 `bash -n`, Node Test Syntax PASS |
| 경계 | `git diff --check` PASS; Swift·Product·Android·Workflow·Package/Lock·Xcode Project Diff 0 |

## 안전 경계

- Summary Schema, Search selector·입력, Stage·Assertion·Exit 65를 변경하지 않았다.
- `eval`, sed·awk·python, locale-dependent character class와 unbounded accepted token을 사용하지 않았다.
- invalid token은 Notice만 생략하며 기존 Raw Log와 Error·원 Exit를 유지한다.
- Commit·Push·PR·GitHub·SSH·서버·GUI·Signing은 수행하지 않았다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인한 후 Commit·Push한다.
2. 새 exact SHA macOS Gate에서 Portable iOS 56/56과 Simulator 진입을 확인한다.
3. Simulator가 진입하면 Search Summary Notice로 실제 input 후보 metadata를 판정한다.
4. 실제 macOS Bash 3.2와 Simulator Runtime·최종 Artifact는 Windows 검증으로 대체하지 않았다.

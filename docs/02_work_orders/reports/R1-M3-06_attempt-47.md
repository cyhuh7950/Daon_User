COMPLETED | R1-M3-06-I007 | Search input bounded 접근성 Summary와 strict CI Notice 진단 구현 | XCTest·Simulator Script·iOS 계약 Test·Progress·Attempt 47 | RED 53/55→GREEN 55/55·Mobile·Android 11/11·Node 319/319·Toolchain·Workflow/Bash·Bundle·Diff PASS | 실제 macOS Search input 후보 metadata·최종 Artifact 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 판정

# R1-M3-06 Attempt 47 결과보고

## 판정

C46 승인 진단 개발 패킷은 `COMPLETED`이며 상태는 `DIAGNOSTIC_READY_PENDING_MACOS_CI`다. Search selector와 입력 동작을 변경하지 않고 SearchField 최종 단일성 guard 실패에서만 bounded·sanitized 접근성 후보를 한 줄로 수집하고, CI는 strict-valid 한 줄만 Notice로 공개한다. 정식 `FAILURE_REPORT`는 0회이며 TP Wave에는 도달하지 않았다.

## 판단 이유

- exact-SHA `c0972019a1b09bbd2f98e0ad301c6c9088de38f7`, Run `30317918702`, Job `90147450287`은 Build·일반 UI 성공과 exact Search button 탭 성공을 증명했다.
- 다음 `SETTINGS_SEARCH_FIELD`에서 `settings.searchFields`가 10초간 0건이었고 line 438 `Settings search field`, `SETTINGS_SEARCH_FIELD_MISSING / revoke / 65`로 종료했다.
- Marker `OPENED AUTH=GRANTED`이며 Artifact에는 후보 type·identifier 정보가 없어 selector 변경보다 공개 접근성 후보 진단이 먼저 필요했다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `settings.searchFields`와 `settings.textFields` accessibility-bound elements만 수집한다.
  - 최대 16건, label·identifier·value 각 48자, 기존 deterministic sanitizer와 `_empty_` marker를 재사용한다.
  - item은 elementType·label·identifier·value·isHittable만 포함한다.
  - SearchField 최종 단일성 guard 실패에서만 Summary를 정확히 한 번 출력하고 기존 Assertion·throw를 그대로 실행한다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - Search Summary가 정확히 한 줄일 때만 prefix·count·4096 전체 길이·16건·48자·허용 문자·item schema를 검증한다.
  - 부재·다중·invalid·injection·oversize·count 불일치는 Notice를 생략하고 기존 Error와 원 Exit 65를 유지한다.
  - 기존 Settings Accessibility Summary와 Notification Open Marker Notice를 보존한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - Swift 후보 범위·상한·필드·금지 정보·failure-only 1회 계약을 고정했다.
  - Bash valid/absent/multiple/invalid/injection/oversize/count mismatch와 성공 무Notice를 실제 Fixture로 검증했다.
- C46 작업지시·Prompt·Progress와 본 Attempt 47 보고서.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C46 RED | iOS 계약 53/55 PASS·2 FAIL: Swift Summary·Bash Notice 부재에서 예상 실패 |
| 첫 GREEN | Bash 계약 PASS, 공용 sanitizer empty marker의 Test slice 불일치 1건으로 54/55 |
| C46 GREEN | Test 범위를 공용 sanitizer까지 확장 후 `verify:ios-native` 55/55 PASS |
| Mobile 전체 | Lint 14 files·Type·Unit 10/10·Contract 15/15·Android 11/11·iOS 55/55 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 319/319 PASS |
| Toolchain·Workflow | Toolchain 7 Manifest·exact Pin·Lockfile, Workflow YAML 2/2 PASS |
| Bash·Syntax | iOS CI Bash 3/3 `bash -n`, Node Test Syntax PASS |
| 경계 | `git diff --check` PASS; Product Host·Bridge·Android·Workflow·Package/Lock·Xcode Project Diff 0 |

## 기존 동작·안전 경계 보존

- exact Search button·`settings.searchFields` selector·`typeText("Daon")`·Stage·Assertion·Exit 65를 변경하지 않았다.
- Summary는 final field guard failure에서만 1회 출력하며 debugDescription·frame·pid·path·환경·키보드 정보는 수집하지 않는다.
- CI는 유효하지 않은 Summary를 공개하지 않고 기존 Raw Log·Error·Notice 순서와 실패 의미를 유지한다.
- Product/Host/Bridge/API/권한·Android·Workflow·Dependency·Lockfile·Project 변경은 없다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인한 후 Commit·Push한다.
2. 새 exact SHA macOS Workflow에서 `DAON_SETTINGS_SEARCH_ACCESSIBILITY_SUMMARY` Notice를 회수해 실제 searchField/textField type·identifier·hittable을 판정한다.
3. 진단 증거 전에는 selector fallback을 추가하지 않는다.
4. 실제 macOS Runtime과 최종 Artifact는 Windows Portable 검증으로 대체하지 않았다.
5. Commit·Push·PR·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.

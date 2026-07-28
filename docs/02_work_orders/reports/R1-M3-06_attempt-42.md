COMPLETED | R1-M3-06-I007 | C41 Settings 접근성 안전 진단·공개 Notice | Swift 제한 요약·Bash 검증 Parser·계약 Test·Progress·Attempt 42 | RED 36/39→GREEN 39/39·iOS 46/46·Mobile·Android 11/11·Node 310/310·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS Summary·Notice Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 42 결과보고

## 판정

C41 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 기존 Notifications Selector·bounded scroll·선택 우선순위와 제품 동작을 변경하지 않고, 최종 `COMPOSITE_ZERO` 직전에만 안전한 Settings 접근성 구조 요약을 1회 생성해 revoke·grant-again XCTest 실패의 공개 Notice로 전달하도록 구현했다. failure count는 0이다.

## 판단 이유

- Head `87b2532a5da412ae0911651dbc8c1a37ada6b1ad`의 iOS Run `30288930289`, Job `90053595796`은 Build·일반 UI Test를 통과했으나 revoke에서 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO / 65`로 종료됐다.
- direct exact, semantic Cell, exact Label, delimiter-anchored composite Cell과 4회 bounded scroll이 모두 0건이므로 추가 Selector 추측 전 실제 Settings 접근성 표면의 제한된 구조 증거가 필요하다.
- 원시 Tree나 `debugDescription` 대신 요소 유형·정규화 Label·Identifier·Hittable만 고정 상한과 안전 문자로 공개하면 진단 가능성과 공개 CI 안전 경계를 함께 유지할 수 있다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `cell`, `button`, `staticText`, `switch` 네 유형의 현재 접근성 요소만 수집한다.
  - 각 항목은 `elementType`, 정규화된 `label`, `identifier`, `isHittable`만 포함한다.
  - ASCII 영문·숫자·고정 구두점 외 문자는 안전 Token으로 치환·인코딩하고 빈 값은 `_empty_`로 표현한다. 줄바꿈·제어문자·`::`·`%`는 출력 형식에 남지 않는다.
  - 항목 16건, 각 Label/Identifier 80자, 전체 4096자 상한을 적용한다.
  - bounded scroll 이후 최종 composite 후보 0건 분기에서만 `DAON_SETTINGS_ACCESSIBILITY_SUMMARY=` 한 줄을 1회 출력한 뒤 기존 `[COMPOSITE_ZERO]`로 Fail-close한다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - revoke·grant-again XCTest 실패에서만 고정 Prefix의 마지막 한 줄을 읽는다.
  - Version·항목 수·필드 순서·허용 유형·문자 집합·필드/전체 길이·Count 일치를 다시 검증한다.
  - 검증 성공 시에만 `::notice::`를 출력하고 불일치는 생략한다. 기존 `::error::CODE=... PHASE=... EXIT=...`와 원 Exit는 유지한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - 진단 부재, 다중 진단의 마지막 행, Workflow 명령 주입, 필드 과다 길이, 항목 과다, Count 불일치, 정상 성공 무Notice를 고정했다.
  - 최종 0건에서만 1회 출력, 4개 유형·허용 필드·상한, Raw Dump·환경·경로·비공개 Selector 금지를 고정했다.
  - 기존 Route Fixture의 Bash 계약 범위를 실제 사용 Helper까지 한정해 Windows 명령 길이 초과를 방지했다. Production 동작은 변경하지 않았다.
- Progress와 본 Attempt 42 보고서.
- 미변경: 기존 direct/semantic/composite Predicate, bounded scroll, 우선순위·다건 Fail-close, Alert/Switch·권한 의미, Product UI·Native Bridge·공개 API, Workflow, Dependency·Lockfile.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C41 RED | iOS 계약 36/39 PASS·3 FAIL: Swift 진단·Bash Notice 계약 부재에서 예상 실패 |
| 첫 GREEN | 37/39 PASS·2 FAIL: 신규 Bash Notice는 PASS, 기존 Route Fixture 명령 길이와 Tuple Type 표현 Test 오탐 |
| C41 GREEN | Test Harness 범위·동등 표현을 정합화한 뒤 iOS 계약 39/39 PASS |
| iOS aggregate | Native Shell·Deep Link·Evidence 46/46 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 46/46 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 310/310 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Node YAML Parser 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Product·Workflow·Package/Lock·Project Diff 0 |

### 기존 동작·안전 경계 보존 근거

- 진단 호출은 기존 최종 `[COMPOSITE_ZERO]` 분기 한 곳뿐이며 후보가 있거나 다른 실패·성공 경로에서는 실행되지 않는다.
- 기존 direct exact, semantic Cell, delimiter-anchored composite Cell Query와 4회 bounded scroll 코드는 변경하지 않았다.
- 진단 Query는 승인된 네 접근성 유형에 한정되고 Selector 선택에 사용되지 않는다.
- Raw Accessibility Tree, `debugDescription`, 환경변수, 경로, 사용자 데이터 전체, Token·Secret을 출력하지 않는다.
- Bash가 안전 형식과 상한을 재검증하지 못하면 Notice만 생략하고 기존 Error·Exit로 Fail-close한다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA macOS iOS Workflow에서 revoke·grant-again을 실행해 실제 Summary 한 줄, 공개 Notice와 기존 Error·Exit 동시 보존을 확인한다.
3. 실제 macOS XCTest 접근성 요소 수집·정규화·GitHub Annotation Runtime과 최종 Artifact는 본 Windows 로컬 환경에서 검증하지 않았다.
4. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.

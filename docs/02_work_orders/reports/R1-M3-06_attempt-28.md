COMPLETED | R1-M3-06-I007 | C27 Permission XCTest 마지막 안전 Stage Marker 구현 | 고정 Marker 18종·Assertion 우선·마지막 허용 Stage 차선·Unknown 최종·계약 Test·Progress·Attempt 28 | 관련 35/35·iOS 42/42·Mobile 전체·Android 11/11·Node 305/305·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS 실패 Stage 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI Stage Annotation 판정

# R1-M3-06 Attempt 28 결과보고

## 판정

C27 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. Permission XCTest의 실제 검증 직전에 고정 Enum 기반 Stage Marker를 남기고, 기존 승인 Assertion Code가 없을 때만 Raw Log의 마지막 exact 허용 Marker를 `STAGE_*` Code로 분류하도록 했다. Marker도 없으면 기존 `UNKNOWN_XCTEST_FAILURE`를 유지한다. C25 Selector·Timeout·검증 순서·권한 동작, Product와 Workflow는 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `8eed1d910db82ccb2e32b7f4800c75193cc395c1`의 iOS Run `30259306171`은 Build와 선행 UI Test가 성공했고 Permission Step이 약 93초 뒤 Exit 65로 실패했다.
- C26 Annotation은 `CODE=UNKNOWN_XCTEST_FAILURE PHASE=grant-initial EXIT=65`로 출력돼 xcodebuild가 Custom Assertion 원문을 노출하지 않는 경우 실패 위치를 구분할 수 없었다.
- 기존 Assertion 문자열을 추측 확장하거나 C25 System Alert·Settings 동작을 바꾸지 않고, 고정 Marker의 마지막 안전 단계를 차선 근거로 사용하는 것이 C27 승인 경계다.
- Quality Run `30259306245`의 공통 검사 Exit 1은 별도 관찰이며, C27 범위에서 원인 추측이나 관련 코드 변경을 하지 않았다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - String 입력이 불가능한 `PermissionXCTestStage` 고정 Enum 18종 추가.
  - Phase/Expected 결속, App Launch/Root, Camera Request/Result, Microphone Request/Result, Notification Request/Result 직전에 Marker 출력.
  - Alert Title/Count/Allow/Dismissal과 Settings Foreground/Notification Row/Switch Read/Toggle/Verify, App Return Root 직전에 Marker 출력.
  - 출력 형식은 `DAON_PERMISSION_XCTEST_STAGE=<fixed enum rawValue>` 한 종류이며 Phase·경로·UDID·URL·사용자 데이터·Element 실제값을 포함하지 않음.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - 18개 `STAGE_*` Code를 실패 Annotation allowlist에 추가.
  - 기존 승인 Assertion 문장 분류를 먼저 수행하고 미일치일 때만 마지막 exact 허용 Marker를 `STAGE_*`로 변환.
  - 미지 Marker와 허용 Marker 접두사 위조는 무시하며, 허용 Marker가 없으면 `UNKNOWN_XCTEST_FAILURE` 유지.
  - 기존 단일 Annotation, Phase·숫자 Exit, `PIPESTATUS` 원 Exit, Raw Evidence Log·Console, Phase별 xcresult 계약 유지.
- `scripts/tests/ios-native-shell.test.mjs`
  - 18개 Marker의 고정 Enum 호출·단일 안전 출력 계약과 Shell `STAGE_*` allowlist를 검증.
  - 기존 Assertion Code 우선, 복수/미지 Marker 중 마지막 허용 Marker 차선, 미지·접두사 위조 Unknown 최종 Fixture 추가.
  - 원 Exit 65와 단일 안전 Annotation, Raw 정보 비노출을 동적 검증.
- Progress와 본 Attempt 28 보고서.
- 미변경: C25 Selector·Timeout·Alert 제목 대기→Count→Allow 순서·Settings Switch·권한 결과, Product Native Host·Bridge, Workflow/Runner, Android, Package/Lockfile, Signing.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C27 RED | 관련 계약 34/35 PASS·1 FAIL: 고정 Stage Marker 부재를 예상대로 재현 |
| 첫 GREEN 복구 | 34/35 PASS·1 FAIL: 기능 Marker Source는 충족했고 Test 금지 정규식이 안전 Enum `stage.rawValue` 명칭을 Raw 값으로 오탐 |
| C27 GREEN | 단일 고정 Enum 출력문 검사로 Test 판별만 정합화 후 관련 계약 35/35 PASS |
| 분류 우선순위 | 기존 `ALERT_COUNT_MISMATCH` Assertion이 Stage보다 우선 PASS |
| 마지막 허용 Stage | 복수·미지 Marker 중 마지막 exact 허용 `STAGE_ALERT_ALLOW`만 단일 Annotation으로 출력 PASS |
| Unknown 안전 경계 | 미지 Marker와 `ALERT_ALLOW_PRIVATE` 접두사 위조 모두 `UNKNOWN_XCTEST_FAILURE`, Raw private path·URL 비노출 PASS |
| 원 증거 계약 | 실패 Exit 65, Phase, 단일 Annotation, Raw Log·Console과 기존 xcresult 경로 보존 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 42/42, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C26과 동일 |
| 전체 Node | 305/305 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Product/Native/Bridge/Info/Project/Workflow/Android/Package/Lock Diff 0; Pods/Build/Artifact/DerivedData 잔존 0 |

### 오류·복구 근거

- RED 34/35는 승인 C27 계약을 선고정한 예상 실패이며 기존 34개 계약은 모두 통과했다.
- 첫 GREEN의 34/35는 Marker 출력 함수의 안전한 `stage.rawValue` 식별자를 금지 정규식이 일반 Raw value로 오인한 Test 오류였다. Marker 구현은 바꾸지 않고 정확한 단일 출력문 1개를 검사하도록 Test만 좁혀 35/35를 확인했다.
- 허용 Marker 정규식은 Marker 끝까지 exact 일치를 요구해 `ALERT_ALLOW_PRIVATE` 같은 접두사 확장을 허용하지 않는다.
- `verify:mobile` 중 읽기 권한이 없는 기존 `services/local-service/.pytest_cache` 탐색 경고가 있었으나 Exit 0이고 관련 파일 변경은 없다.
- Windows Portable 검증은 실제 macOS Xcode 26.6 Console Marker 노출, 실패 Stage와 Artifact를 대체하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료를 확인하고 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행한다.
3. 첫 Permission 실패 시 기존 Assertion Code 또는 마지막 `STAGE_*`, Phase와 원 Exit가 단일 안전 Annotation에 표시되는지 확인한다.
4. Stage Code가 확인되면 해당 Stage의 실제 xcresult·Raw Evidence를 기준으로 후속 판단하며 Selector·Timeout·권한 동작을 추측 수정하지 않는다.
5. 세 Permission Phase와 Evidence Manifest까지 성공하면 Phase A 상태를 다음 Gate 기준으로 판정한다.
6. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

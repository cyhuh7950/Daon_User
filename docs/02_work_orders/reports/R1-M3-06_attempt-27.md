COMPLETED | R1-M3-06-I007 | C26 Permission XCTest 안전 실패 Annotation 구현 | Phase Raw Log·allowlist Code 분류·단일 Annotation·PIPESTATUS Fixture·Progress·Attempt 27 | 관련 34/34·iOS 41/41·Mobile 전체·Android 11/11·Node 304/304·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS Exit 65 Assertion Code 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI Annotation 판정

# R1-M3-06 Attempt 27 결과보고

## 판정

C26 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. Permission XCTest 원문을 Phase별 Evidence Log와 Console에 보존하고, 실패 시 승인된 Code·Phase·숫자 원 Exit만 포함하는 GitHub Error Annotation을 정확히 1건 출력하도록 했다. C25의 XCTest, Alert·Settings Selector, 권한 전환 동작은 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `68b50efd7d058767a50840471030c5f780c172f6`의 Quality Gate Run `30257206564`는 성공했다.
- iOS Run `30257206671`은 unsigned Build와 선행 UI Test가 성공했고 첫 Permission XCTest가 약 97초 뒤 Exit 65로 실패했다.
- 상세 GitHub Log와 Artifact는 인증 차단으로 확인할 수 없어 실제 실패 Assertion은 미확정이다. 따라서 Selector나 권한 동작을 추측 수정하지 않고 다음 exact-SHA 실행에서 안전한 분류 근거를 확보하는 C26 경계를 적용했다.
- Raw XCTest 문장, 경로, UDID, 사용자 데이터와 URL은 공개 Annotation에 포함하지 않고 Evidence Log에만 보존해야 한다.

## 조치

### 변경 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
  - `permission-grant-initial.log`, `permission-revoke.log`, `permission-grant-again.log`에 각 XCTest Raw Output을 저장하면서 `tee`로 Console 출력 유지.
  - 고정 Assertion 문장만 Alert 제목·개수·Allow·닫힘, Settings foreground·Notification row·Switch·값 전환, 앱 복귀 Root, Production 결과 누락의 10개 allowlist Code로 분류.
  - 미일치 문장은 `UNKNOWN_XCTEST_FAILURE`로 Fail-safe 분류.
  - 실패 Annotation 형식을 `::error::CODE=<allowlist> PHASE=<allowlist> EXIT=<number>`로 고정하고 XCTest 실패당 정확히 1건만 출력.
  - `PIPESTATUS`에서 `xcodebuild`와 `tee` Exit를 분리해 XCTest 원 Exit를 우선 반환. 진단 함수 실패는 원 XCTest 실패를 성공으로 바꾸지 않으며, XCTest 성공·`tee` 실패 때는 `tee` Exit 반환.
  - 기존 `set -Eeuo pipefail`, ERR/EXIT Trap, Phase별 xcresult와 Cleanup 유지.
- `scripts/tests/ios-native-shell.test.mjs`
  - 알려진 Alert count 실패, 미지 실패, 성공의 Bash Fixture를 추가.
  - Raw Log·Console 보존, 원 Exit 65, 단일 안전 Annotation, Unknown Fallback, 성공 시 Annotation 0건을 동적 검증.
  - 10개 allowlist Code, `PIPESTATUS`, Annotation 고정 형식과 Raw 정보 비출력 계약을 정적 검증.
- Progress와 본 Attempt 27 보고서.
- 미변경: C25 Permission XCTest와 Alert·Settings·Switch·앱 복귀 동작, Product Native Host·Bridge·권한 결과, Deep Link, Workflow/Runner, Android, Package/Lockfile, Signing.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C26 RED | 관련 계약 33/34 PASS·1 FAIL: Phase Raw Log·분류·`PIPESTATUS` Helper 부재를 예상대로 재현 |
| C26 GREEN | 관련 계약 34/34 PASS |
| 알려진 실패 Fixture | Raw Console·Log 일치, Exit 65, `ALERT_COUNT_MISMATCH`·`grant-initial`·`65`만 포함한 Annotation 정확히 1건 PASS |
| 미지 실패 Fixture | Private path·UDID·URL 원문은 Raw Log에 보존, Annotation은 `UNKNOWN_XCTEST_FAILURE`·`revoke`·`65`만 1건 PASS |
| 성공 Fixture | Raw Console·Log 일치, Exit 0, Error Annotation 0건 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 41/41, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C25와 동일 |
| 전체 Node | 304/304 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; XCTest/Product/Native/Bridge/Info/Project/Workflow/Android/Package/Lock Diff 0; Pods/Build/Artifact/DerivedData 잔존 0 |

### 오류·복구 근거

- RED 33/34는 승인 C26 계약을 선고정한 예상 실패이며 기존 33개 계약은 모두 통과했다.
- Bash Fixture는 실제 `xcodebuild`의 65와 성공 0을 모사해 Pipeline 원 Exit, Raw Log와 Annotation 수·내용을 함께 검증했다.
- `verify:mobile` 중 읽기 권한이 없는 기존 `services/local-service/.pytest_cache` 탐색 경고가 있었으나 Exit 0이고 관련 파일 변경은 없다.
- Windows Portable 검증은 실제 macOS Xcode 26.6 Assertion, Artifact 생성과 GitHub Annotation 노출을 대체하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료를 확인하고 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행한다.
3. Permission XCTest 실패 시 Phase Raw Log Artifact 존재, 원 Exit 보존과 안전 Annotation 정확히 1건을 확인한다.
4. Annotation Code를 기준으로 대응하되, `UNKNOWN_XCTEST_FAILURE`이면 인증 가능한 Raw Artifact를 먼저 확인하고 Selector·Assertion을 추측 수정하지 않는다.
5. 세 Permission Phase와 Evidence Manifest까지 성공하면 Phase A 상태를 다음 Gate 기준으로 판정한다.
6. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

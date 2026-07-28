COMPLETED | R1-M3-06-I007 | 알림 설정 공개 URL Open 결과·직전 권한 상태 안전 진단 구현 및 중단 복구 | iOS Host·Simulator 검증 Script·계약 Test·Progress·Attempt 44 | RED 41/43→GREEN 50/50·Mobile·Android 11/11·Node 314/314·Toolchain·Workflow/Bash·Bundle·Diff PASS | 실제 macOS Simulator Marker·Notice와 권한 OFF/ON Runtime·최종 Artifact 미확인 | 어울1의 Diff 검토·Commit/Push·exact-SHA macOS CI 판정

# R1-M3-06 Attempt 44 결과보고

## 판정

C43 승인 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 예기치 않은 중단 지점의 Diff를 보존해 인수했고, 제품 UI·Bridge 8 Selector·Settings Selector·권한 의미를 변경하지 않은 채 진단 계약과 전체 Portable 회귀를 완료했다. 정식 `FAILURE_REPORT`는 0회다.

## 판단 이유

- C42 exact-SHA `99ece954bf7cf25014fb144acd7fbb94469aac17`, Run `30295391636`, Job `90075005931`은 Build·일반 UI Test 성공 후 revoke에서 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO / 65`로 종료했다.
- 기존 증거만으로 공개 URL의 `UIApplication.open` 완료 Bool과 호출 직전 `UNAuthorizationStatus`를 구분할 수 없으므로 C43은 고정 Marker 진단만 추가한다.
- Marker와 CI Notice는 허용 값만 공개하고 Raw unified log·경로·환경·비밀값 및 원래 Exit 의미를 노출하거나 변경하지 않는다.

## 조치

### 변경 범위

- `apps/mobile/ios/Daon/DaonIOSHost.swift`
  - `openNotificationSettings`에서 호출 직전 `getNotificationSettings` 결과를 기존 고정 권한 값으로 정규화한다.
  - iOS 16+/15.1 URL 선택과 URL 실패 Reject를 유지하고 Open Completion에서 `OPENED|FAILED`와 권한 상태만 한 줄 `NSLog` 후 원 Bool을 resolve한다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - Permission XCTest 실패 때만 최근 5분 Daon Process unified log의 정확 Marker를 별도 임시 파일로 수집한다.
  - 마지막 유효 한 줄을 허용 값·길이로 재검증해 Notice로 공개하고 수집 실패·부재·invalid·주입은 생략한다.
  - 단일 임시 파일은 `unlink`로 정리하며 기존 Error와 Exit 65를 보존한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - 권한 조회→URL 선택→Open→Marker→원 Bool Resolve 순서와 4개 Auth 값, OPENED/FAILED, 부재·다중·invalid·주입·수집 실패·성공 무Notice 계약을 고정한다.
  - C42의 한 줄 `resolve($0)` 표현은 named Bool을 같은 값으로 resolve하는 동등 계약으로 정합화했다.
- C43 작업지시·Prompt·Progress와 본 Attempt 44 보고서.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C43 RED | iOS 계약 41/43 PASS·2 FAIL: Swift Marker·Simulator Notice 부재에서 예상 실패 |
| 첫 GREEN | 신규 C43 계약은 PASS했으나 기존 Bool Resolve 표현·광역 임시 파일 삭제 금지 계약 2건과 표현 충돌 |
| C43 GREEN | 동등 Bool Resolve Test와 단일 파일 `unlink`로 정합화 후 `verify:ios-native` 50/50 PASS |
| Mobile 전체 | Lint 14 files·Type·Unit 10/10·Contract 15/15·Android 11/11·iOS 50/50 PASS |
| Bundle | Android 927,506 bytes SHA-256 `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921,716 bytes SHA-256 `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |
| 전체 Node | 314/314 PASS |
| Toolchain·Workflow | Toolchain 7 Manifest·exact Pin·Lockfile, Workflow YAML 2/2 PASS |
| Bash·Syntax | iOS CI Bash 3/3 `bash -n`, Node Test Syntax PASS |
| 경계 | `git diff --check` PASS; Product UI·Bridge·XCTest Selector·Android·Workflow·Package/Lock·Xcode Project Diff 0 |

## 기존 동작·안전 경계 보존

- 기존 generic 설정 진입, 알림 전용 공개 URL 선택, Bridge 8 Selector, Permission UI와 Settings Selector를 변경하지 않았다.
- `registerForRemoteNotifications`, APNs Capability·Entitlement·Signing, private URL, TCC/Settings DB 조작, 재설치 또는 성공 변환을 추가하지 않았다.
- unified log는 Permission 실패에만 조회하고 공개 Notice는 고정 Marker 한 줄 이외를 포함하지 않는다.
- Workflow·Dependency·Lockfile·Project·Android 변경은 없다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인한 후 Commit·Push한다.
2. 새 exact SHA macOS iOS Workflow에서 Marker Notice를 회수해 `OPENED|FAILED`와 직전 권한 상태를 판정한다.
3. 실제 macOS Simulator 권한 OFF/ON Runtime과 최종 Artifact는 Windows Portable 검증으로 대체하지 않았다.
4. Commit·Push·PR·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.

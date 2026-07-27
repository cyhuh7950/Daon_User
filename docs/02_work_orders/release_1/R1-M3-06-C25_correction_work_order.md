# R1-M3-06-C25 수정 작업지시서 — Notification 실제 시스템 권한 전환

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `26` |
| 사유 | C24 exact-SHA에서 camera·microphone Privacy 설정은 통과했으나 iOS 26.6 Simulator가 `simctl privacy ... notifications`를 `Operation not permitted`로 거부함 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-26.md` |

## 2. 확인된 증거와 설계 판단

- exact Head `e5422934d52328597e8071105d549a032ef618b8`의 Quality Gate Run `30254672711`은 SUCCESS다.
- iOS Run `30254672664`은 Build와 System Open UI Test까지 SUCCESS이고 첫 Permission Phase에서 `DAON_SIM_FAILED_STAGE=PERMISSION_GRANT_INITIAL`, `DAON_SIM_FAILED_PERMISSION_SERVICE=notifications`, Exit 1을 기록했다.
- Camera·Microphone 호출은 알림 호출 전 통과했으므로 기존 `simctl privacy` 경로를 유지한다.
- Apple XCTest 지침에 따라 예상된 시스템 권한 Modal은 우회하거나 숨기지 않고 UI Test의 직접 대상으로 다룬다.
- 앱 재설치로 알림 상태를 초기화하면 승인된 `grant → revoke → re-grant` 동일 설치 전환 계약을 약화하므로 금지한다.

## 3. 필수 구현

1. `run_permission_phase`에서 지원되지 않는 notifications `simctl privacy` 호출만 제거하고 camera·microphone의 순서·행동은 보존한다.
2. Permission XCTest에 고정 Phase 입력을 전달한다: `grant-initial`, `revoke`, `grant-again`. 미지정·미허용 값은 Fail-close한다.
3. `grant-initial`의 notification 버튼은 Production `requestPermission`을 실제 호출하고, SpringBoard의 예상 Notification 권한 Alert 존재를 직접 확인한 뒤 승인 버튼을 탭한다. Alert나 승인 버튼이 없으면 실패한다.
4. `revoke`와 `grant-again`은 앱의 Production `앱 권한 설정 열기`를 통해 실제 Settings App으로 이동하고 Notification 설정 화면의 `Allow Notifications`를 각각 OFF/ON으로 전환한 뒤 앱으로 복귀한다. Private URL Scheme·Defaults/TCC DB 수정·좌표 탭·요소 Index 추측·재설치를 사용하지 않는다.
5. Settings 전환 전후에 실제 Switch 값과 앱 복귀 `runningForeground`·Root Ready를 확인한다. 이미 목표 상태이면 목표 상태 확인 후 계속할 수 있으나 반대 상태를 성공으로 간주하지 않는다.
6. 세 Phase 모두 Camera·Microphone·Notification Production 버튼을 실제 탭하고 UI의 OS 결정 결과 `GRANTED`/`DENIED`를 확인한다. 기존 3개 xcresult와 exact-SHA Evidence 계약을 유지한다.
7. Simulator 언어 의존 Selector는 GitHub Runner의 고정 장치 언어를 명시적으로 설정·검증하거나, 허용된 실제 접근성 Label 집합을 사용해 Fail-close한다. 범용 firstMatch/index/좌표로 우회하지 않는다.
8. Product Native Host·Bridge·권한 결과 매핑·Settings 공개 API·Deep Link·Lifecycle·Signing은 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 unsupported notifications privacy 호출, Phase 입력 부재, System Alert/Settings 전환 부재 계약 RED
- 구현 후 notifications privacy 호출 0건, camera·microphone 기존 호출 유지, 3개 Phase exact 입력·예상 Modal 직접 처리·Settings OFF/ON·Production 결과 검증 계약 GREEN
- Alert·Settings 요소 부재, 예상 상태 불일치, 앱 복귀 실패는 모두 원 XCTest 실패를 보존
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Simulator Script, Permission XCTest, Runner/Workflow의 고정 Phase·언어 입력에 필요한 최소 범위, 관련 계약 Test, Progress와 Attempt 26 보고서뿐이다.
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속이다.


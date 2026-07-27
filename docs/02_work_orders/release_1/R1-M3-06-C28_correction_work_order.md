# R1-M3-06-C28 수정 작업지시서 — Permission Phase 입력 결속 세분화

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `29` |
| 사유 | C27 exact-SHA에서 마지막 Marker가 `PHASE_EXPECTED_BINDING`으로 확인되어 App Launch 전 Phase/Expected 입력 결속 구간의 실패로 좁혀졌으나, 현재 Marker 하나가 다섯 Guard를 묶어 실패 입력을 판정할 수 없음 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-29.md` |

## 2. 확인된 증거

- exact Head `e35164675e1aaf2b145d84d830dfd86aa501ccfd`의 iOS Run `30261307378`은 Toolchain·Portable 회귀·Pods·Simulator 생성·unsigned Build·일반 UI Test를 모두 통과했다.
- Permission Step은 `grant-initial`에서 Exit 65이고 공개 Annotation은 `CODE=STAGE_PHASE_EXPECTED_BINDING PHASE=grant-initial EXIT=65`다.
- 코드 순서상 마지막 Marker 뒤에는 `DAON_PERMISSION_PHASE` 존재·허용값, `DAON_PERMISSION_EXPECTED` 존재·허용값, Phase-Expected 일치 검증이 있고 그 다음에야 `APP_LAUNCH_ROOT` Marker가 기록된다.
- 따라서 Product 실행·Alert·Settings Selector를 수정할 근거는 아직 없으며, Phase 입력 결속 내부만 세분화해야 한다.
- 같은 SHA의 Quality Run `30261307332`은 Toolchain·Rust 사전검증 후 공통 Gate에서 두 번째 연속 Exit 1이다. 현재 공개 Annotation에는 세부 실패가 없고 인증 제한으로 Artifact 원문을 회수하지 못했으므로, C28에서는 Product 수정 근거로 사용하지 않는다.

## 3. 필수 작업

1. 기존 `PHASE_EXPECTED_BINDING` 뒤 각 성공 경계에 고정 Marker를 추가해 최소한 다음을 분리한다: Phase 환경값 존재, Phase 허용값 변환 성공, Expected 환경값 존재, Expected 허용값 검증 성공, Phase-Expected 일치 성공.
2. 기존 `APP_LAUNCH_ROOT`와 이후 Marker·Assertion·Selector·Timeout·검증 순서·권한 동작은 변경하지 않는다.
3. Marker는 고정 Enum Literal만 사용하고 실제 환경값·경로·UDID·URL·사용자 데이터는 출력하지 않는다.
4. Shell의 기존 Assertion 우선·마지막 허용 Marker 차선·Unknown 최종, 단일 안전 Annotation, 원 Exit, Raw Log와 xcresult 계약을 유지하면서 신규 Marker만 Allowlist에 추가한다.
5. exact-SHA 재실행 전에는 Phase 전달 방식을 추측해 변경하지 않는다. 신규 Marker로 끊긴 Guard가 확정된 뒤 후속 수정 여부를 어울1이 판단한다.
6. Quality 반복 실패는 `npm run verify:quality-gate`를 현재 Worktree에서 재실행하고 생성된 로컬 결과의 실패 항목을 보고한다. 재현되지 않으면 `NOT_REPRODUCED_LOCALLY`로 기록하고 Quality Workflow·정책·제품 코드는 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 다섯 Phase 입력 결속 Marker와 Shell Allowlist 계약 RED
- 구현 후 마지막 허용 Marker 분류, 기존 Assertion 우선순위, Unknown·성공·원 Exit Fixture PASS
- 기존 iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- Quality Gate 로컬 재실행 결과와 증거 JSON의 실패 항목을 Attempt 29에 기록
- 허용 변경은 Permission XCTest 고정 Marker, Simulator Script Allowlist, 관련 계약 Test, Progress와 Attempt 29뿐이다.
- Quality Workflow·정책·제품 코드, C25 Alert/Settings 동작, Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 변경 또는 수행하지 않는다.


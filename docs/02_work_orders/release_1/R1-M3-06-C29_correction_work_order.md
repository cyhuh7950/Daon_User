# R1-M3-06-C29 수정 작업지시서 — Permission Phase 고정 XCTest 진입점

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `30` |
| 사유 | C28 exact-SHA에서도 마지막 Marker가 최초 `PHASE_EXPECTED_BINDING`에 머물러 `DAON_PERMISSION_PHASE`가 XCTest Process에 존재하지 않음이 확정됨 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-30.md` |

## 2. 확인된 증거

- exact Head `b8960d2493ac08d58d9cc970de8d5b550e657ec8`의 iOS Run `30262705084`은 Toolchain·Portable 회귀·Pods·Simulator·unsigned Build·일반 UI Test를 모두 통과했다.
- Permission Step은 `grant-initial`에서 Exit 65이고 Annotation은 다시 `CODE=STAGE_PHASE_EXPECTED_BINDING PHASE=grant-initial EXIT=65`다.
- C28은 `DAON_PERMISSION_PHASE` 존재 Guard 성공 직후 `PHASE_ENV_PRESENT`를 기록하도록 했으나 해당 Marker가 없다. 따라서 첫 Guard에서 종료됐다.
- `verify-simulator.sh`의 부모 Process 환경변수 결속은 XCTest Process 입력 계약이 되지 않는다. Alert·Settings·Product 실행 전에 실패하므로 해당 동작 수정 근거는 없다.

## 3. 설계 판단과 필수 작업

1. 환경변수 전달에 의존하지 않고 세 Phase를 고정 XCTest 진입점으로 분리한다: grant-initial, revoke, grant-again.
2. 각 진입점은 고정 `PermissionPhase`와 고정 Expected(`GRANTED`, `DENIED`, `GRANTED`)를 공통 Private Helper에 전달한다. Runtime 문자열·동적 Selector로 Phase를 해석하지 않는다.
3. Shell은 기존 고정 Phase Loop에서 Phase별 허용 XCTest Method 이름을 명시 매핑하고 `-only-testing`에 해당 Method만 전달한다. 사용자 입력이나 Raw 값을 Method 이름으로 조합하지 않는다.
4. 같은 설치·세 Phase 순서·camera/microphone `simctl privacy`·notification 실제 System Alert와 Production Settings OFF/ON·세 xcresult·Phase/Exit Annotation 계약을 유지한다.
5. C28 입력 환경 Guard와 그 전용 Marker는 더 이상 실행 계약이 아니므로 제거한다. `PHASE_EXPECTED_BINDING`은 공통 Helper가 받은 고정 Phase/Expected 결속 직전 Marker로 유지하고, 일치 검증 성공 후 `PHASE_EXPECTED_MATCHED`를 유지한다.
6. APP_LAUNCH_ROOT 이후 Assertion·Selector·Timeout·순서·권한·제품 코드는 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 세 고정 XCTest 진입점, Phase별 Shell Method 매핑, 환경변수 비의존 계약 RED
- 구현 후 세 Phase가 각각 정확히 하나의 고정 Method를 선택하고 공통 Helper에 올바른 Phase/Expected를 전달
- `DAON_PERMISSION_PHASE`와 `DAON_PERMISSION_EXPECTED`를 Test Process 계약으로 사용하지 않음
- 기존 Assertion 우선·Stage 차선·Unknown 최종·원 Exit·단일 Annotation·Raw 비노출 Fixture PASS
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Permission XCTest Phase 진입점, Simulator Script Phase별 Test 선택, 관련 계약 Test, Progress와 Attempt 30뿐이다.
- Quality Workflow·정책·제품 코드, Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 변경 또는 수행하지 않는다.


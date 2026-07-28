# R1-M3-06-C51 수정 작업지시서

- 상태: APPROVED
- 실행: Attempt 52
- issue: `R1-M3-06-I007`
- 기준 HEAD: `2263040e12b0e64059adc67f639b51c296cbc44a`

Run `30351582383`, Job `90250064273`에서 pull-down과 Daon 입력은 성공했으나 기존 exact 결과 selector가 0건이었다. 결과 selector는 변경하지 않고 모든 결과 missing/ambiguous 종료 직전 단일 helper로 `DAON_SETTINGS_SEARCH_RESULT_SUMMARY=v1`을 정확히 한 번 출력한다.

- 후보 순서: cell → button → staticText → other, 전체 최대 24.
- label/identifier/value 중 하나가 nonempty이거나 hittable인 후보만 기존 sanitizer로 token 48, `isHittable`과 함께 기록한다. 전체 4096.
- debug/frame/pid/path/env/keyboard/coordinate/index/dump/private API를 금지한다.
- 기존 결과 selector/predicate/wait/tap, C50 pull-down, Stage/Assertion/Exit65를 변경하지 않는다.
- Simulator Script는 strict-valid 단일 Result Summary만 별도 Notice로 공개한다. count 0..24, elementType 4종, token48, total4096, schema/delimiter/injection/중복행을 검증하며 C48 Bash3.2 방식과 원 Exit65를 보존한다.
- TDD로 Swift bounded·failure-only·exactly-once와 Bash valid/empty/absent/multiple/invalid/injection/oversize/count mismatch를 검증한다.
- 허용: DaonUITests.swift, verify-simulator.sh, ios-native-shell.test.mjs, C51 문서/Progress/Attempt52.
- 금지: Product/Host/Bridge/API, selector/predicate/tap/input, Android/Workflow/deps/lock/project/signing과 외부 작업.

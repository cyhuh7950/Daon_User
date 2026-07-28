# R1-M3-06-C45 수정 작업지시서

## 문서 상태

- 상태: APPROVED
- 작업: R1-M3-06-C45 / Attempt 46
- Issue: R1-M3-06-I007
- 기준 HEAD: `507e7825e2428ad7cc18c43f1ec1e4da3361d420`
- 작성일: 2026-07-28
- Writer: 어울2 단독

## 확정 근거

- exact-SHA Run `30316280643`, Job `90142395318`
- Build와 일반 UI Test는 성공했다.
- C41 summary의 exact Search button은 `hittable=1`이고 제품 Marker는 `OPENED AUTH=GRANTED`였다.
- `DaonUITests.swift` line 341의 COMPOSITE_ZERO `XCTFail`이 typed sentinel throw보다 먼저 기록되어 Search fallback이 성공해도 XCTest 실패를 회복할 수 없는 제어 흐름 결함이다.

## 목표

제품 코드·공식 URL·권한 의미를 변경하지 않고 recoverable COMPOSITE_ZERO 경로의 선행 `XCTFail`만 제거한다. iOS 26 Search fallback 성공 가능성을 보존하면서 다른 모든 모호성·비 Hittable 경계는 즉시 Fail-close한다.

## 구현 계약

1. initial direct Switch 부재 뒤 exact Notifications row의 최종 COMPOSITE_ZERO를 iOS 26 Search로 넘기는 호출에서만 `XCTFail` 없는 `notificationSettingsRowAbsent` sentinel로 전달한다.
2. COMPOSITE_AMBIGUOUS, label/semantic ambiguity, non-hittable 등 다른 경계는 기존 즉시 Fail-close를 유지한다.
3. iOS 26 미만은 동일 COMPOSITE_ZERO assertion/message/code/Exit 65를 명시적으로 발생시킨다.
4. Search 결과 후 Daon app surface에서 row가 0건이면 선행 COMPOSITE_ZERO `XCTFail` 없이 최종 `Daon notification settings surface` assertion만 발생시켜 `SETTINGS_SEARCH_APP_SURFACE_MISSING`으로 분류한다.
5. 함수 인자 또는 별도 probe helper 중 최소 방법을 사용한다. silent absence 허용은 위 두 catch 가능한 경로에만 제한하고 기본 동작은 기존 Fail-close다.
6. TDD RED는 recoverable zero branch의 선행 `XCTFail` 부재, pre-iOS26 explicit COMPOSITE_ZERO fail, Search app surface exact fail을 고정하고 기존 52 tests를 보존한다.

## 허용 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
- `scripts/tests/ios-native-shell.test.mjs`
- 코드·Stage 변경이 실제 필요한 경우만 `apps/mobile/ios/ci/verify-simulator.sh`
- C45 작업지시·Prompt·Progress·Attempt 46 보고서

## 금지 범위

- Product/Host/Bridge/API/Android/Workflow/dependencies/lock/project/signing 변경
- Commit/Push/PR/GitHub/SSH/server/GUI 작업
- 기존 변경 되돌림, 좌표·Index·partial/regex selector, private URL, TCC/Settings DB 조작

## 단계와 진행 복구 기록

- 진행 기록: `docs/04_test_reports/release_1/R1-M3-06_progress.md`
- 착수, RED, GREEN, 오류·복구, 검증, 종료 직전에 시각·단계·상태·변경 파일·명령/테스트 결과·원인·복구·다음 작업을 기록한다.

## 필수 검증

1. `npm run verify:ios-native`
2. `npm run verify:mobile`
3. 전체 Node Test
4. Toolchain baseline
5. Workflow YAML 2개 parse
6. iOS CI Bash 3개 `bash -n`
7. 변경 Node test syntax
8. Bundle hash·size 유지 확인
9. `git diff --check`
10. Product/Host/Bridge/API/Android/Workflow/Package/Lock/Xcode Project 보호 경계 Diff 0

## 완료 보고

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음 판단`

실제 macOS iOS 26 Runtime과 최종 Artifact를 Portable 검증으로 대체하지 않는다. 정식 실패보고 0회와 TP 미도달 상태를 유지한다.

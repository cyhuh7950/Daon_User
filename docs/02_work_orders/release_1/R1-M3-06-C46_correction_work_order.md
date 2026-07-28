# R1-M3-06-C46 수정 작업지시서

## 문서 상태

- 상태: APPROVED
- 작업: R1-M3-06-C46 / Attempt 47
- Issue: R1-M3-06-I007
- 기준 HEAD: `c0972019a1b09bbd2f98e0ad301c6c9088de38f7`
- 작성일: 2026-07-28
- Writer: 어울2 단독

## 확정 근거

- exact-SHA Run `30317918702`, Job `90147450287`
- Build·일반 UI Test 성공
- Permission revoke에서 exact `com.apple.settings.search` 버튼 탭 성공 후 `SETTINGS_SEARCH_FIELD`에서 10초간 `settings.searchFields` 후보 0건
- line 438 `Settings search field`, `CODE=SETTINGS_SEARCH_FIELD_MISSING PHASE=revoke EXIT=65`
- Product Marker `OPENED AUTH=GRANTED`
- 기존 Artifact·xcresult·주변 Log에는 실제 text-input 후보 type·identifier 정보가 없다.

## 목표

selector를 추측하거나 변경하지 않고 Search 버튼 탭 뒤 실제 text-input 접근성 후보를 bounded·sanitized 한 줄 진단으로 수집한다.

## 구현 계약

1. Product/Host/Bridge/API/권한·Search selector/입력·기존 Stage/Assertion/Exit 65를 변경하지 않는다.
2. `SETTINGS_SEARCH_FIELD` 최종 단일성 guard 실패 때만 `DAON_SETTINGS_SEARCH_ACCESSIBILITY_SUMMARY=v1|count=N|items=...`를 정확히 1행 출력한다.
3. 후보는 `settings.searchFields`와 `settings.textFields`의 accessibility-bound elements만 수집한다.
4. 각 item은 `elementType(searchField/textField)`, sanitized `label`, `identifier`, `value`, `isHittable`만 포함한다. debugDescription·frame·pid·path·환경·사용자 데이터·키보드 내용은 금지한다.
5. 후보 총 최대 16개, 각 문자열 최대 48자, deterministic sanitization과 empty marker를 사용한다. Count는 실제 출력 item 수와 일치한다.
6. 중복 element는 type+label+identifier+value+hittable 기준으로 deterministic 제거할 수 있다.
7. Summary 생성 실패가 원 XCTest failure와 Exit 65를 가리지 않게 한다.
8. Simulator Script는 Permission 실패 때 마지막 유효 Summary 한 줄만 엄격 검증해 `::notice::`로 공개한다. 부재·다중·invalid·injection·oversize이면 Notice를 내지 않는다.
9. 기존 accessibility summary와 notification open marker Notice를 보존한다.

## 허용 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
- `apps/mobile/ios/ci/verify-simulator.sh`
- `scripts/tests/ios-native-shell.test.mjs`
- C46 작업지시·Prompt·Progress·Attempt 47 보고서

## 금지 범위

- 실제 selector fallback 추가
- Product/Android/Workflow/dependencies/lock/project/signing 변경
- Commit/Push/PR/GitHub/SSH/server/GUI 작업
- 기존 변경 되돌림, private URL, TCC/Settings DB 조작

## 단계별 진행 기록

- 기록 파일: `docs/04_test_reports/release_1/R1-M3-06_progress.md`
- 착수, RED, GREEN, 오류·복구, 테스트 완료, 종료 직전에 시각·단계·상태·변경 파일·명령/결과·원인·복구·다음을 기록한다.

## 필수 검증

1. TDD RED→GREEN: Swift bounded summary·failure-only exactly once·금지 field 0
2. Bash valid/absent/multiple/invalid/injection/oversize에서 원 Exit 65 보존
3. `npm run verify:ios-native`와 `npm run verify:mobile`
4. 전체 Node Test·Toolchain·Workflow YAML·Bash syntax·Node syntax
5. Bundle hash·size, `git diff --check`, 보호 경계 Diff 0

## 완료 보고

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음 판단`

실제 macOS Search input 접근성 Runtime과 최종 Artifact를 Portable 검증으로 대체하지 않는다. 정식 실패보고 0회와 TP 미도달 상태를 유지한다.

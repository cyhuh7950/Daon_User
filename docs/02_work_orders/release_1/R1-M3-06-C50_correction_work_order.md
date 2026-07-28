# R1-M3-06-C50 수정 작업지시서

## 문서 상태

- 상태: APPROVED
- 실행: Attempt 51
- 동일 문제: `R1-M3-06-I007`
- 기준 HEAD: `241e1eb693ed1df815df2df644565c8e01b97921`

## 확인된 증거와 목적

- Run `30349204845`, Job `90242461826`은 Portable 59/59·Build/general UI를 통과했다.
- Permission revoke에서 Surface Summary는 `Search_and_Look_Up` 설명 TextView와 identifier를 기록했고 SearchField/TextField는 0건이었다.
- `com.apple.settings.search`는 검색 입력 버튼이 아니라 Search and Look Up 설정 항목으로 확정됐다.
- 잘못된 identifier query·대기·tap을 제거하고 공개 pull-down 제스처로 SearchField 노출을 bounded 검증한다.

## 구현 계약

- `.settingsSearchButton` Stage 위치는 로그/Bash 호환을 위해 유지한다.
- Stage 뒤 최대 6회 `settings.swipeDown()`만 수행하고 각 swipe 직후 기존 hittable searchFields query를 재평가한다.
- 정확히 1건이면 즉시 중단하고, 2건 이상이면 즉시 ambiguous Fail-close, 6회 뒤 0건이면 기존 `SETTINGS_SEARCH_FIELD_MISSING` 경로로 종료한다.
- `com.apple.settings.search` query·대기·tap을 제거하고 다시 사용하지 않는다.
- 고정 sleep, coordinate/index, private API, predicate 확대와 Search and Look Up TextView/other/button 입력 우회를 금지한다.
- 검색어 입력, Daon exact 결과, 알림 설정 Surface, Stage/Assertion/Exit 65, C46/C49 진단을 보존한다.
- TDD로 잘못된 identifier/tap 제거, 최대 6회 bounded swipeDown, 매회 exact-one 조기중단, zero/ambiguous Fail-close와 후속 계약을 고정한다.

## 변경 범위와 검증

- 허용: `apps/mobile/ios/DaonUITests/DaonUITests.swift`, `scripts/tests/ios-native-shell.test.mjs`, C50 문서·Progress·Attempt 51.
- 금지: Product/Host/Bridge/API, Simulator Script/Workflow, Android, 의존성/Lock/Project/Signing과 외부 작업.
- iOS Native, Mobile, 전체 Node, Toolchain, Workflow YAML, Bash, Bundle, Diff·보호 경계를 검증한다.

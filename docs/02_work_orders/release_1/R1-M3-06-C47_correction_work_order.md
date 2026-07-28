# R1-M3-06-C47 수정 작업지시서

## 문서 상태

- 상태: APPROVED
- 작업: R1-M3-06-C47 / Attempt 48
- Issue: R1-M3-06-I007
- 기준 HEAD: `b461f6cbacc592cae32ecd638311928ca120206c`
- 작성일: 2026-07-28
- Writer: 어울2 단독

## 확정 근거

- exact-SHA Run `30343791949`, Job `90225154593`
- Simulator 진입 전 Portable iOS contracts에서 C46 valid Summary Fixture가 macOS Bash 3.2에서 Notice 0건으로 거부되어 54/55 실패
- Windows Git Bash에서는 55/55 PASS
- 단일 ERE의 `{1,48}` token 반복과 문자 Class가 macOS Bash 3.2에서 이식되지 않는 Parser 결함이며 Product·Swift 진단 결함이 아니다.

## 목표와 구현 계약

1. Swift Summary·selector·Stage/Assertion/Exit 65·Product를 변경하지 않는다.
2. `report_settings_search_accessibility_notice` item 검증만 Bash 3.2 호환으로 최소 수정한다.
3. 구조 ERE는 elementType, comma-delimited label/identifier/value, isHittable만 분리한다.
4. 각 token은 별도 함수에서 `${#token}` 1..48과 ASCII letters/digits `_ . + - / { }`만 Bash 3.2 호환 `case`로 허용한다.
5. comma·semicolon·pipe·colon·percent·whitespace와 기타 문자를 거부한다.
6. `eval`, external sed/awk/python, locale-dependent class와 unbounded value를 사용하지 않는다.
7. count 0 `_none_`, max 16, overall 4096, 단일 Summary, `::`/`%`, count/schema와 원 Exit 65를 유지한다.
8. TDD로 Bash 3.2 호환 구현 형태와 valid/invalid Fixture를 고정한다. `u{AC00}`은 허용하고 comma·space·colon·49자는 거부한다.

## 허용 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
- `scripts/tests/ios-native-shell.test.mjs`
- C47 작업지시·Prompt·Progress·Attempt 48 보고서

## 금지 범위

- Swift/Product/Android/Workflow/dependencies/lock/project/signing 변경
- selector fallback 또는 Summary Schema 변경
- Commit/Push/PR/GitHub/SSH/server/GUI 작업

## 진행 기록과 필수 검증

- Progress: `docs/04_test_reports/release_1/R1-M3-06_progress.md`
- 착수·RED·GREEN·오류/복구·검증·종료 직전에 단계별 근거를 기록한다.
- `npm run verify:ios-native`, `npm run verify:mobile`, 전체 Node, Toolchain, Workflow YAML, Bash 3개, Node syntax, Bundle, Diff와 보호 경계를 검증한다.

## 완료 보고

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음 판단`

실제 macOS Bash 3.2 CI와 Simulator Runtime을 Portable 검증으로 대체하지 않는다. 정식 실패 0회와 TP 미도달 상태를 유지한다.

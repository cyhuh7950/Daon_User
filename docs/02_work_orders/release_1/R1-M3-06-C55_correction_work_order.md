# R1-M3-06-C55 수정 작업지시서

## 승인 기준

- Attempt 56, 기준 HEAD `1067fca3603ed7e5c8cdf2b6e5c52c23cdfd6749`.
- 동일 문제 `R1-M3-06-I007`, 정식 실패 0회, C55 `INCOMPLETE` 0회, TP 미도달.
- 근거: Run `30363396550`, Job `90288408830`.

## 목표와 구현 계약

Apps-local 검색 결과 선택에서 `settings.buttons.matching(identifier: "com.sinsan.daon")` exact query를 최우선으로 사용한다. Wait는 exact app button이 hittable이면 즉시 성공한다. 후보가 1건이면 선택하고, 2건 이상이면 `Settings Daon app result`로 즉시 Fail-close한다. 0건일 때만 기존 exact-label cell 우선·descendant fallback을 그대로 수행한다.

Runner identifier `com.sinsan.daon.uitests.xctrunner`, label prefix/contains, index·`firstMatch`·coordinate·regex 선택을 금지한다. 승인 Application ID literal은 변경하지 않는다. Result tap 이후 app settings surface·switch·Notifications row, C51 진단, Stage·Assertion·Exit 65를 보존한다.

## 허용 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
- `scripts/tests/ios-native-shell.test.mjs`
- C55 작업지시서·프롬프트·Progress·Attempt56 보고서

Product, Simulator Script, Host/Bridge/API, Android, Workflow, dependency/lock/project/signing과 Commit·Push·외부 실행은 변경하거나 수행하지 않는다.

## TDD·검증·진행 기록

exact Bundle button의 Wait 우선, 1건 선택, 2건 이상 Fail-close, 0건 legacy fallback, Runner 제외, 금지 패턴과 후속 계약 보존을 RED 테스트로 고정한 뒤 최소 구현한다. iOS Native, Mobile 전체, 전체 Node, Toolchain, Workflow YAML, iOS Bash, Node syntax, Bundle, Diff와 보호 경계를 검증한다.

`docs/04_test_reports/release_1/R1-M3-06_progress.md`에 착수, RED, GREEN, 오류·복구, VERIFY, END를 기록하고 표준 완료 보고서를 제출한다.

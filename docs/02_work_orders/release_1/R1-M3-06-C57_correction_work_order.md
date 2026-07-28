# R1-M3-06-C57 수정 작업지시서

## 승인 기준

- Attempt 58, 기준 HEAD `c27c668b5b58bddd355b18b5b9ed3118fc4cb2d7`.
- 동일 문제 `R1-M3-06-I007`, 정식 실패 0회, C57 `INCOMPLETE` 0회, TP 미도달.
- 근거: exact-SHA Run `30369438084`, Job `90309102055`.

## 목적과 구현 계약

C56 Marker의 revoke `before=on`, 일반 `tap()` 후 `after=on` 증거에 따라 stale element는 배제하고 exact switch의 기본 hittable point가 실제 토글 영역을 누르지 못한 원인으로 판정한다. `requireFreshNotificationSwitch`가 반환한 exact switch element에만 종속된 우측 중앙 normalized coordinate `CGVector(dx: 0.9, dy: 0.5)`를 상태 변경 필요 시 정확히 1회 탭한다.

before가 이미 target이면 switch tap은 0회다. 다르면 normalized coordinate tap은 1회이며 재탭·retry를 추가하지 않는다. Application·screen absolute coordinate, frame 기반 전역 좌표, index·prefix·contains selector, Settings 재시작과 권한 API 우회를 금지한다. 기존 before/after/final fresh Marker, 5초 wait, final assert, unsupported·0·2+ fail-close와 Exit 65를 그대로 유지한다.

switch 영역 tap은 작은 전용 helper로 제한하고 revoke·grant-again 공통 경로에서 사용한다. 요구되지 않은 리팩터링을 하지 않는다.

## 허용 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
- `scripts/tests/ios-native-shell.test.mjs`
- C57 작업지시서·프롬프트·Progress·Attempt58 보고서

Simulator parser와 Marker schema, 제품 코드·공개 계약·Signing·Phase B·타 플랫폼·dependency/lock/project를 변경하지 않는다.

## TDD·검증·전달

exact fresh switch에 종속된 dx/dy 0~1 내부 normalized coordinate tap 1회, 기존 plain `tapTarget.tap()` 제거, 금지된 retry·absolute coordinate 부재를 RED로 먼저 고정한다. GREEN 뒤 C56·C55·C54 인접 계약을 함께 검증한다. 이후 `verify:ios-native`, `verify:mobile`, 전체 Node, Toolchain, Workflow YAML 2개, iOS Bash 3개, Node syntax, Diff, Bundle identifier/hash와 Product/protected boundary를 검증하고 Exit·건수를 보고한다.

Progress에 MAIN 판정·착수·RED·GREEN·오류/복구·VERIFY·종료 직전을 append한다. 완료 시 `판정 → 판단 이유 → 조치` 보고서를 작성하고 허용 Diff를 단일 목적 Commit으로 branch `codex/r1-m3-06`에 Push한다. exact SHA와 Clean·원격 SHA 일치를 어울1에게 인계하며 CI 완료 판정은 어울1이 수행한다.

# R1-M3-06-C56 수정 작업지시서

## 승인 기준

- Attempt 57, 기준 HEAD `07fb987b1f718643c90fa008e35b7dc65d2c015b`.
- 동일 문제 `R1-M3-06-I007`, 정식 실패 0회, C56 `INCOMPLETE` 0회, TP 미도달.
- 근거: exact-SHA `07fb987`, Run `30365939432`의 `SETTINGS_SWITCH_VALUE_FAILED`.

## 목적과 구현 계약

Allow Notifications switch의 stale `XCUIElement` 가능성을 제거한다. tap 직전, 검증 과정과 최종 판정은 매번 Settings의 stable identifier `ALLOW_NOTIFICATIONS_ID` exact query로 새 조회하고, 0건일 때만 기존 영문·한글 exact label query를 사용한다. 각 우선 경로 결과는 정확히 1건만 허용하며 0건·복수·non-hittable은 기존 fail-close 오류로 종료한다.

revoke·grant 양쪽 공통 경로에서 `before`·`after`·`final` 상태를 제한된 `DAON_NOTIFICATION_SWITCH_STATE=v1` Marker로 출력한다. Marker는 phase, point, element count, 제한 토큰 identifier·label, raw value type과 `on|off|unsupported` 정규화 상태만 포함하며 전체 hierarchy나 민감정보를 출력하지 않는다. NSNumber와 문자열 `1/0/On/Off/켜짐/꺼짐` 의미를 유지하고 나머지는 fail-close한다.

Tap은 상태 변경이 필요할 때 해당 phase에서 최대 1회만 수행한다. coordinate tap·재탭·Settings 재시작·권한 API 우회와 새 fallback을 금지한다. 새 조회로 target이 확인되지 않으면 기존 `SETTINGS_SWITCH_VALUE_FAILED`를 유지하고 확보된 Marker를 남긴다.

## 허용 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
- `apps/mobile/ios/ci/verify-simulator.sh`
- `scripts/tests/ios-native-shell.test.mjs`
- C56 작업지시서·프롬프트·Progress·Attempt57 보고서

앱 제품 코드, 공개 계약, Signing, Phase B, Android·타 플랫폼, dependency/lock/project와 관련 없는 파일을 변경하지 않는다.

## TDD·검증·전달

새 조회, stable identifier 우선·label fallback, 단일성 fail-close, before/after/final Marker, strict Parser/Notice, 1회 Tap, 정규화와 금지 fallback을 정적·Runtime Fixture RED로 먼저 고정하고 최소 구현한다. Targeted, `verify:ios-native`, `verify:mobile`, 전체 Node, Toolchain, Workflow YAML, iOS Bash, Bundle identifier·hash, protected boundary와 Diff를 검증하고 Exit·건수를 보고한다.

Progress에 착수·RED·GREEN·오류/복구·VERIFY·종료 직전을 append한다. 완료 시 `판정 → 판단 이유 → 조치`의 Attempt57 보고서를 작성하고 허용 Diff를 점검한 뒤 branch `codex/r1-m3-06`에 단일 목적 Commit·Push한다. exact SHA를 어울1에게 인계하며 CI 완료 대기·판정은 어울1이 수행한다.

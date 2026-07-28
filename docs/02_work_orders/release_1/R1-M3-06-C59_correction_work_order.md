# R1-M3-06-C59 수정 작업지시서

## 승인 기준

- Attempt 60, 기준 HEAD `9096741de3b0f18d568a477f5e1bcc3e43294266`.
- 새 문제 `R1-M3-06-I009`, 정식 실패 0회, C59 `INCOMPLETE` 0회, TP 미도달.
- 근거: exact branch `npm run verify:independence -- --no-write` 결과 components 8, edges 10, package files 10, scanned files 125, violations 3, Exit 1.

## 목적과 구현 계약

독립성 위반 세 건은 모두 `scripts/tests/ios-native-shell.test.mjs`의 sanitizer fixture가 실제 런타임 민감 경로 비노출을 검증하기 위해 소스에 직접 연속 기록한 `/Users/private` literal이다. 테스트 의미와 sanitizer 검증 강도를 유지하면서 저장소 Source에는 외부 개인 절대경로 토큰을 직접 남기지 않는다.

세 fixture의 런타임 raw 문자열은 기존 `/Users/private` 또는 그 prefix를 포함하는 의미를 그대로 보존한다. 작은 test-local helper 또는 fragment 조합으로 런타임에만 생성한다. sanitizer가 허용된 메시지는 남기고 private path·UDID·URL·raw secret를 출력하지 않는 기존 assertion을 유지하거나 강화한다.

Scanner regex, independence policy, 예외·ignore·설정은 변경하지 않는다. 관련 없는 Test 정리·리팩터링을 하지 않는다.

## 허용 범위

- `scripts/tests/ios-native-shell.test.mjs`
- C59 작업지시서·프롬프트·Progress·Attempt60 보고서

Product·Swift·Simulator Script·Workflow·Policy·Scanner·Package·Lock·Config를 변경하지 않는다.

## TDD·검증·전달

변경 전 `npm run verify:independence -- --no-write`의 violations 3·Exit 1을 RED로 기록한다. 소스 literal을 분리한 뒤 동일 명령이 violations 0·Exit 0이어야 한다. 관련 sanitizer fixture test와 전체 `verify:ios-native`를 실행한다.

이후 `verify:mobile`, 전체 Node, 가능한 현재 환경의 `verify:quality-gate`, Toolchain, Workflow YAML 2개, iOS Bash 3개, Node syntax, Diff, Bundle identifier/hash와 Product/protected boundary를 검증한다. Quality Gate가 다른 환경 원인으로 실패하면 C59와 분리해 정확한 단계·원인을 보고한다.

Progress에 MAIN 판정·착수·RED·GREEN·오류/복구·VERIFY·종료 직전을 append한다. 완료 시 `판정 → 판단 이유 → 조치`의 Attempt60 보고서를 작성하고 허용 Diff를 단일 목적 Commit으로 branch `codex/r1-m3-06`에 Push한다. exact SHA와 Clean·원격 SHA 일치를 어울1에게 인계하며 GitHub Quality/iOS 판정은 어울1이 수행한다.

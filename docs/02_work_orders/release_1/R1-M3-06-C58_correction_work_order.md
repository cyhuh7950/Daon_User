# R1-M3-06-C58 수정 작업지시서

## 승인 기준

- Attempt 59, 기준 HEAD `3e6a46b5436e82810966cd35f87af4d2b5f34147`.
- 새 문제 `R1-M3-06-I008`, 정식 실패 0회, C58 `INCOMPLETE` 0회, TP 미도달.
- 근거: exact-SHA Run `30372486985`, Job `90319669000`.

## 목적과 구현 계약

C57 permission 세 단계는 revoke `on→off→off`, grant-again `off→on→on`으로 성공했다. 각 permission XCTest가 종료 시 앱을 terminate하므로 후속 `LIFECYCLE_TERMINATE`가 실행 중 Process를 전제하면 `found nothing to terminate` Exit 3으로 실패한다.

lifecycle 검증을 이전 테스트의 잔존 Process와 분리한다. permission 세 단계 뒤 lifecycle terminate 전에 정확한 Bundle `com.sinsan.daon`을 `xcrun simctl launch`로 명시 실행하고, launch stdout을 기존 Evidence Directory의 고정 파일에 보존한다. 이어 `wait_for_route_with_evidence Home` 성공으로 준비 상태를 증명한 뒤 기존 fail-close terminate를 실행한다. 이후 기존 relaunch, Home ready, `lifecycle_state`의 `created|foreground|active` 검증을 그대로 유지한다.

준비 launch와 ready에는 제한된 `LIFECYCLE_PREPARE_LAUNCH`, `LIFECYCLE_PREPARE_READY` Stage를 사용하고 allowlist·정적 계약을 함께 갱신한다. 준비 launch/ready는 각 1회이고 기존 lifecycle terminate/relaunch도 각 1회다. retry, sleep, Process 우회, `|| true`, permission 흐름 변경을 금지한다. 최종 terminate, pgrep Process 확인, 최종 Log/Binary Scan과 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE` 상태 계약을 보존한다.

## 허용 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
- `scripts/tests/ios-native-shell.test.mjs`
- 필요한 경우 `scripts/tests/ios-phase-a-evidence.test.mjs`
- C58 작업지시서·프롬프트·Progress·Attempt59 보고서

Swift·Product·권한 Marker·Simulator Signing·Phase B·타 플랫폼·dependency/lock/project를 변경하지 않는다.

## TDD·검증·전달

permission 세 단계 뒤 `LIFECYCLE_PREPARE_LAUNCH → exact bundle launch·고정 Evidence → LIFECYCLE_PREPARE_READY → Home ready → LIFECYCLE_APPEARANCE → strict terminate → relaunch → Home ready → lifecycle_state` 순서, 두 신규 Stage allowlist, 준비/terminate/relaunch 각 1회와 lifecycle 구간 `|| true`·retry·sleep 부재를 RED로 먼저 고정한다. GREEN 뒤 기존 lifecycle·permission·C57 인접 계약을 함께 검증한다.

이후 `verify:ios-native`, `verify:mobile`, 전체 Node, Toolchain, Workflow YAML 2개, iOS Bash 3개, Node syntax, Diff, Bundle identifier/hash와 Product/protected boundary를 검증하고 Exit·건수를 보고한다.

Progress에 MAIN 판정·착수·RED·GREEN·오류/복구·VERIFY·종료 직전을 append한다. 완료 시 `판정 → 판단 이유 → 조치`의 Attempt59 보고서를 작성하고 허용 Diff를 단일 목적 Commit으로 branch `codex/r1-m3-06`에 Push한다. exact SHA와 Clean·원격 SHA 일치를 어울1에게 인계하며 CI 완료 판정은 어울1이 수행한다.

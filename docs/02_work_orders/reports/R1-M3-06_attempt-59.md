COMPLETED | R1-M3-06-I008 | permission 종료와 독립된 lifecycle 준비 launch·Home ready 뒤 strict terminate 수행 | Simulator Script·iOS 정적 계약·C58 문서·Progress·Attempt59 | RED0/1→iOS69/69·Mobile·Node333/333·Toolchain/YAML/Bash/Bundle/Diff PASS | 실제 macOS exact-SHA lifecycle Runtime·최종 Artifact 미확인 | 단일 Commit·Push 후 어울1 CI 판정

# R1-M3-06 Attempt 59 결과보고

## 판정

C58 승인 수정은 `COMPLETED`이며 상태는 `LIFECYCLE_PREPARE_PENDING_MACOS_CI`다. permission 세 XCTest의 종료 상태에 의존하지 않도록 lifecycle 검증 전 exact Bundle을 명시 실행하고 Home ready를 증명한 뒤 기존 strict terminate를 수행하게 했다. 정식 `FAILURE_REPORT`와 C58 `INCOMPLETE`는 0회이며 TP Wave에는 도달하지 않았다.

## 판단 이유

- exact-SHA Run `30372486985`, Job `90319669000`에서 permission revoke와 grant-again은 성공했으나, 각 XCTest 종료 시 앱이 terminate되어 후속 `LIFECYCLE_TERMINATE`가 `found nothing to terminate` Exit 3으로 중단됐다.
- `LIFECYCLE_PREPARE_LAUNCH`에서 정확한 `${BUNDLE_ID}`를 실행하고 stdout을 `${EVIDENCE_DIR}/lifecycle-prepare-launch.log`에 보존한다.
- `LIFECYCLE_PREPARE_READY`에서 기존 `wait_for_route_with_evidence Home`으로 실행·복원 준비를 fail-close 증명한 뒤 기존 terminate→relaunch→Home ready→lifecycle_state 검증을 유지했다.
- 준비 launch/ready는 각 1회, lifecycle terminate/relaunch도 각 1회다. lifecycle 구간에 retry·sleep·Process 우회·`|| true`를 추가하지 않았다.

## 변경 결과

- `apps/mobile/ios/ci/verify-simulator.sh`
  - 제한 Stage `LIFECYCLE_PREPARE_LAUNCH`, `LIFECYCLE_PREPARE_READY`를 allowlist에 추가했다.
  - permission 세 단계 뒤 lifecycle 전용 launch·고정 Evidence·Home ready를 추가했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - C58 순서·횟수·strict terminate·금지 우회·최종 계약을 TDD로 고정했다.
  - 전역 Home ready 횟수를 초기·lifecycle prepare·relaunch 3회로 정합했다.
- C58 작업지시서·프롬프트·Progress·본 Attempt59 보고서를 기록했다.

## 테스트 결과

| 검증 | 결과 |
|---|---|
| C58 TDD | RED 0/1, 구현 후 lifecycle·permission·C57 인접 5/5 PASS |
| iOS Native | 69/69 PASS |
| Mobile | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 69/69, Bundle PASS |
| 전체 Node | 333/333 PASS |
| Toolchain | 7 npm manifests·exact pins·lockfiles PASS |
| Workflow·Syntax | YAML 2/2, Git Bash 3/3, Node syntax, `git diff --check` PASS |
| Bundle | Android 927506 bytes `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921716 bytes `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616` |

`verify:mobile`은 기존 `services/local-service/.pytest_cache` EPERM 읽기 경고를 출력했으나 Exit 0이었고 C58 관련 변경은 없다.

## 조치

1. 허용된 6개 파일만 단일 목적 Commit으로 묶어 `codex/r1-m3-06`에 Push한다.
2. 어울1은 exact SHA의 macOS CI에서 lifecycle prepare launch·Home ready·strict terminate·relaunch와 최종 Artifact를 판정한다.
3. Windows Portable 검증은 실제 macOS Simulator 증거를 대체하지 않는다.

# R1-M4-05-C03 Linux Process Tree 종료·검증 무한대기 중대 보완 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-05-C03`.
- Branch `codex/r1-m4-05`, 기준 HEAD `ede6bd547c2f1b78bcd64dd1a8e3b8fbe875555c`, 시작 Clean.
- PR #25 Quality Run `30414400123`, Job `90457607749`의 60분 timeout과 종료 시 orphan process 증거를 적용한다.
- 어울2가 이 Worktree와 범위의 유일한 Writer다. PR·CI 재실행·Merge는 어울1 소유다.

## 판정과 단일 목표

- 판정: `MAJOR_GAP / CORRECTION_REQUIRED`.
- 증거: `Run common quality gate`에서 `verify:api-runtime`가 57분 55초 동안 반환하지 않았고 Job 종료 시 `uv`, `python3`, `next-server`가 orphan으로 정리됐다. Assertion 실패 출력은 없었다. Windows 실제 Process 검증은 통과했지만 Linux CI 종료 계약은 성립하지 않았다.
- 직접 원인 가설은 POSIX에서 `npm` 부모에만 SIGTERM을 보내고 자식 `next-server`가 stdout pipe를 보유한 채 남아 `process.stdout.read()`가 EOF를 기다리는 경로다. 가설을 재현·검증하고 증거에 따라 수정한다.
- 목표: API·Next 실제 Process 검증이 Windows와 Linux 모두에서 bounded time 안에 종료되고, 소유한 전체 Process tree와 Listener를 남기지 않으며 기존 기능·보안 검증을 그대로 수행하게 한다.

## 허용·제외 범위

- 허용: `services/api/tests/runtime_process_probe.py`, `scripts/verify-api-runtime.mjs`, 해당 Process lifecycle 테스트·검증기·M4-05 evidence, C03 진행·완료보고. 원인 증거상 꼭 필요한 최소 실행 스크립트만 추가 허용한다.
- 제외: Quality 검사 삭제·skip·완화, timeout 추가 연장, API/BFF 업무 동작·공개 Route·인증/권한 계약 변경, iOS 변경, dependency·Lockfile 변경, 전체 구조 재작성.
- C01의 Cookie 최소전달·CSRF·abort·Trace 계약과 실제 API/Next·same-port restart·credential 비반사 검증을 모두 보존한다.

## Process lifecycle 계약

- POSIX에서는 Next launcher와 그 자식 전체를 작업 소유 Process group/session으로 격리하고 정상 종료 시 group 전체에 bounded graceful signal을 전달한다. Windows의 기존 별도 Process group 의미도 보존한다.
- 부모 종료만으로 성공 판정하지 않는다. 자식 Process와 Listener가 모두 사라진 뒤에만 종료 성공으로 판정한다.
- stdout/stderr 수집은 자식이 pipe를 보유해도 무기한 block하지 않게 bounded `communicate` 또는 동등한 수단을 사용한다. timeout 시 전체 소유 tree를 강제 정리하고 안정 오류로 종료한다.
- finally/error 경로도 전체 소유 tree를 bounded cleanup한다. 시스템의 다른 npm/python/next Process를 이름 기반으로 종료하지 않는다.
- API 첫 실행·재실행, Next 실행 각각의 정상 exit code·graceful marker·same-port/listener release 검증을 유지한다.

## TDD·검증

- RED에서 POSIX launcher 자식이 pipe/listener를 보유하는 fixture를 만들어 기존 종료가 bounded 계약을 위반함을 증명한다. 실제 60분을 기다리는 테스트는 금지한다.
- GREEN에서 정상 종료, stubborn child 강제 정리, 오류/finally cleanup, 재시작 및 unrelated process 비영향을 자동 검증한다.
- `npm run verify:api-runtime -- --no-write`와 실제 Linux 또는 WSL/ysna-server 동등 POSIX 환경에서 `verify:api-runtime`를 bounded time으로 직접 실행하고 exit 0, owned process 0, listener 0을 증명한다. 서버 사용 시 기존 배포·공용 자원은 변경하지 않고 격리 경로만 사용한다.
- BFF 9, API Runtime 10, Identity/Authorization/Audit, OpenAPI, Web build, Quality runner 관련 회귀를 실행한다. 실제 Process 검사를 mock 결과로 대체하지 않는다.
- Quality Workflow timeout은 60분으로 유지하되, PR 재실행은 어울1이 수행한다.

## 진행·보고

`docs/04_test_reports/release_1/R1-M4-05-C03_progress.md`에 착수, CI 증거, RED/재현, 구현, 오류·복구, POSIX 실제 Process, 회귀, 종료 직전을 단계마다 기록한다. 완료 후 C03 완료보고를 작성하고 단일 보완 Commit을 Push한 뒤 Local/Remote SHA·Clean과 표준 상태를 보고한다.

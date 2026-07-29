# R1-M4-05-C03 완료보고서

## 판정

- `COMPLETED`
- Issue ID: `R1-M4-05-C03`
- 목표였던 Windows/Linux 실제 API·Next Process의 bounded 종료, 전체 소유 Process tree·Listener 정리, 기존 Runtime·BFF·보안 검증 보존을 충족했다.

## 확인 원인과 수정

- POSIX에서 launcher 부모만 종료하면 자식이 상속한 stdout pipe와 Listener를 계속 보유했고, 부모 `wait()` 뒤 무제한 `stdout.read()`가 EOF를 기다리는 경로를 실제 fixture로 재현했다.
- API·Next launcher를 POSIX 새 session/process group과 Windows 별도 process group으로 격리했다.
- 정상 종료는 소유 group 전체에 bounded graceful signal을 보내고 `communicate(timeout=...)`로 출력을 회수한다. 시간 초과와 finally 경로는 정확한 PID tree/group만 강제 정리하며 다른 npm·Python·Next process를 이름으로 종료하지 않는다.
- Next는 npm wrapper 대신 설치된 Node와 `next/dist/bin/next`를 직접 실행해 불필요한 부모 process 계층을 제거했다.
- 허용한 정상 종료값은 Windows `0`, `0xC000013A`; POSIX `0`, `-SIGTERM`, `128+SIGTERM(143)`뿐이다. 실제 성공 evidence에는 플랫폼별 제어 종료를 `0`으로 정규화했다.

## TDD·실제 환경 검증

- RED: WSL 실제 fixture에서 부모 exit 0 뒤 자식 pipe/listener가 남아 종료에 `3.988초`가 걸렸고 bounded `<2초` 계약이 실패했다.
- GREEN: 정상 자식, stubborn 자식 강제 정리, finally cleanup, unrelated session 비영향을 POSIX 실제 process로 검증했다.
- Windows full Runtime verifier: 최종 no-write Exit 0, 약 `30.7초`; API Runtime `10/10`, lifecycle `2 PASS/4 POSIX skip`, BFF `9/9`, 실제 API·Next production process, Web build, same-port restart, owned process/listener `0`.
- WSL actual POSIX full Runtime verifier: 외부 상한 `180초` 안의 Exit 0, 약 `76.8초`; API Runtime `10/10`, lifecycle `6/6`, BFF `9/9`, 실제 API·Next production process, Web build, same-port restart, owned process/listener `0`.
- 마지막 fixture 안전장치 변경 후 lifecycle만 재실행해 Windows `2 PASS/4 skip`, WSL `6/6 PASS`를 확인했다.

## 관련 회귀

- Identity `18/18`, Authorization `22/22`, Audit `13/13` PASS.
- OpenAPI: paths `44`, operations `67`, schemas `53`, errors `28`; 계약 SHA-256 `75BCF8D80DC45479B0A120161E215D4E924E4BD26ACB12ABFEED3BD4B6B2E357` 유지.
- Quality runner + product foundation `38/38` PASS.
- Independence: components `8`, edges `10`, package files `10`, scanned files `159`, violations `0`.
- `git diff --check` PASS, 대상 파일 비밀정보 패턴 `0건`.
- iOS, Workflow, 제품 Route/API 동작, dependency와 lockfile는 변경하지 않았다.

## 산출물·증거

- `services/api/tests/runtime_process_probe.py`
- `services/api/tests/process_tree_fixture.py`
- `services/api/tests/test_runtime_process_lifecycle.py`
- `scripts/verify-api-runtime.mjs`
- `docs/03_evidence/release_1/R1-M4-05/bff-network-summary.json`
- BFF evidence SHA-256: `1ADEF9294A5D6D2D9FA892D6AD145D1414DF41A84892B5A8678AF71B4E74E943`
- Runtime process evidence SHA-256: `6EA11228E78C0CF10B4627A4082F326B187B13539FBDF0B8AA96B9C919055441`

## 제외·후속

- 전체 Quality Gate를 다시 실행하지 않았다. PR #25 CI 재실행과 Merge는 작업지시대로 어울1이 수행한다.
- WSL 검증은 `/tmp/daon-c03-work-ede6`와 격리된 Node/Python 환경에서 수행했으며 기존 배포·공용 자원은 변경하지 않았다.
- 실제 GitHub Actions Linux runner 재검증은 Push 후 어울1의 PR CI 단계에서 확인한다.

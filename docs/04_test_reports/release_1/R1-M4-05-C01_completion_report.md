# R1-M4-05-C01 완료보고

## 판정

**COMPLETED — BFF Session credential 최소 전달, Cookie Write same-origin/CSRF, Client abort·timeout 전파와 요청별 안전 Trace를 구현하고 실제 API·Next production process까지 검증했다.**

## 판단 이유

- Browser Cookie 전체를 전달하지 않고 `__Host-daon_session` 하나만 검증·재구성한다. 다른 Cookie는 폐기하고 duplicate·CR/LF·4 KiB 초과 Session은 upstream 호출 전에 안전 거부한다.
- POST·PUT·PATCH·DELETE는 `Origin`이 실제 Request URL origin과 정확히 같고, 제공된 `Sec-Fetch-Site`가 `same-origin`일 때만 진행한다. 결손·null·다중·scheme/host/port 불일치는 fail-close한다.
- Client signal과 bounded timeout을 하나의 Abort scope로 결합했다. request body read, upstream fetch, response body read를 모두 취소 경계에 연결하고 client cancel 499와 timeout 504를 구분한다.
- 정상·오류 종료의 timer와 client listener를 `finally`에서 정리한다. 설정·예기치 않은 Route 오류는 client 제공 Trace를 재사용하지 않고 요청마다 새 opaque Trace를 Header·Envelope에 동일하게 반환한다.
- 실제 Next production process에서 same-origin Session 200·Write 200, cross-origin Write 403·해당 Trace의 upstream Audit event 0건을 확인했다. 응답·로그·Client bundle credential/내부 URL hit는 0건이다.

## 변경 결과

- `apps/web/lib/bff-api-proxy.js`: Cookie 최소 파싱, CSRF, Abort scope, 단계별 안전 오류와 cleanup
- `apps/web/app/bff/api/[...path]/route.js`: 설정·예기치 않은 오류의 요청별 고유 Trace와 안전 Envelope
- `scripts/tests/api-bff-runtime.test.mjs`: 기존 3개를 보존한 총 9개 BFF 보안·회귀 테스트
- `services/api/tests/runtime_process_probe.py`: 실제 same-origin Write와 cross-origin upstream 0건·credential 비반사 검증
- `scripts/verify-api-runtime.mjs`: BFF 9개 정본 계수
- `packages/contracts/openapi/v1/openapi.json`: 신규 BFF 안전 오류 6종 정합
- OpenAPI·BFF deterministic evidence와 C01 진행 기록 갱신

## TDD와 검증 결과

| 검증 | 결과 |
| --- | --- |
| TDD RED | 기존 3 PASS, 신규 5 FAIL로 결함 재현 |
| BFF 최종 unit | 9/9 PASS |
| request body client abort·timeout | 499·504 안전 분류, upstream 0 |
| upstream fetch client abort | 결합 signal 실제 abort, 499 |
| response body client abort·timeout·unexpected | body cancel, 499·504·502 안전 분류 |
| timer·listener cleanup | add/remove 1:1, timeout 경과 후 upstream abort 0, 비동기 잔존 경고 0 |
| Runtime HTTP | 10/10 PASS |
| 실제 API·Next production process | PASS · exit 0 · same-port restart · listener/process 0 |
| Next production build | PASS |
| same-origin/cross-origin Write | 200/403, 거부 Trace upstream Audit 0 |
| OpenAPI | 44 path · 67 operation · 53 schema · 28 error PASS |
| Workspace | 34/34 PASS |
| Identity | 18/18 PASS |
| Authorization | 22/22 PASS |
| Audit | 13/13 PASS |
| Independence | 157 file · violation 0 (`--no-write`) |
| Syntax·Diff | Node/Python compile PASS, `git diff --check` PASS |

## Evidence

- `docs/03_evidence/release_1/R1-M4-05/bff-network-summary.json` · SHA-256 `6D155BAE2879EC64630BCF6867F8010CE536C92AEAD3DE612632AC43EE9FFE3A`
- `docs/03_evidence/release_1/R1-M4-05/runtime-process-summary.json` · SHA-256 `6EA11228E78C0CF10B4627A4082F326B187B13539FBDF0B8AA96B9C919055441`
- `docs/03_evidence/release_1/R1-M4-01/openapi-contract-summary.json` · SHA-256 `0410CB159CF6ABE3D5B1285BCCBD2C934489844EFEA293F935FFBA84A5E8279E`
- `docs/04_test_reports/release_1/R1-M4-05-C01_progress.md`

## 오류·복구 기록

- Worktree 기존 `.venv` lock 접근 거부는 제품 실패가 아니며, 격리된 `C:\tmp\Daon_User-r1-m4-05-c01-venv`를 사용해 동일 lock 기준으로 검증했다.
- Next production probe의 최초 valid Write 403은 Request Host가 `localhost`인데 probe Origin이 `127.0.0.1`이어서 정확 일치 규칙이 정상 차단한 결과였다. 실제 Browser URL·Origin을 `localhost`로 일치시켜 200을 확인했다.
- OpenAPI evidence writer의 일시 EPERM은 deterministic summary를 계산해 `apply_patch`로 동일 내용 반영 후 읽기 검증 PASS로 복구했다.

## 제외 범위·미해결 사항

- FastAPI Identity·Authorization·Audit 동작, Browser UI, 인증 방식, 공개 Route와 외부 dependency는 변경하지 않았다.
- GUI Chrome·외부 HTTPS Reverse Proxy·ysna-server·PR·CI·Merge·배포는 이번 보완 범위가 아니며 수행하지 않았다.
- 제품 구현과 지정 로컬 검증의 미해결 사항은 없다.

## 다음 조치

- 같은 Branch에 작업지시서·프롬프트를 포함한 단일 C01 보완 Commit을 Push한다.
- PR·CI·Merge와 독립 재검토는 어울1이 수행한다.

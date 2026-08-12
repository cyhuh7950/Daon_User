# R1 사용자 제품 분리 Task 8 C02 진행 기록

## 2026-08-12T10:49:08+09:00 · 착수·승인·기준선 확인

- 상태: `IN_PROGRESS_TDD_RED`
- Issue ID: `R1-USER-PRODUCT-SEPARATION-TASK8-01-I001`
- 사용자 승인 ID: 현재 대화 `2026-08-12 Native BFF 분리 승인`
- 공식 정본: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`
- Branch / HEAD / origin master: `codex/task8-clean-build-c01` / `ac8544762e5a97e286694c3631cf5d4f507790c4` / `23174814465fe25362877b558453eb658dde4476`
- Branch 생성은 상위 도구가 정본 경계를 오인해 거부했으므로 우회하지 않고 현재 공식 checkout에서 진행한다.
- staged 0, 기존 삭제 25, Cargo worktree/HEAD blob `bbf68886c6a96f9201994714be5dc13b8275d855` 동일, Native Evidence와 기존 미추적 문서 보존.
- 승인 문서 SHA-256: 설계 `0E3E26BFA1DECFC1356C363C4B1F504477693F2D5CB55F3AB6C5681FE8D0C3C1`; 계획 `09366BA98F8655BABC736ED039B4ED7B393FCE77513DD62497E13FA5318C2BB8`; C02 Work Order `49A1CC77F3D982FCBBD7B77792E39786EC612AB968DAC0DD74DD741127B1E643`; prompt `570D0288C370165ED51F0200B439932EC2716323142E561D175AD1E904169571`.
- 기존 기록 SHA-256: Task8 Progress `E18B72F68A0EA247DFA745189E21BB32B0081503E7509B7DAD01A686F839BB4A`; Task8 Completion `9ADDD7324A69A704222983FD0541BAB688D6CF573059268B4089F08BF797B813`; D01 Progress `C9E9A6A7FFB7E8AF898703937C2850AA0347BA27634CEB40CF636318AAFDA4D4`; D01 Completion `8ED4EC5F5AA23C62A1F7B557952A26D4A9D30FC25C35E356EBC675592C6278F0`; C01 Progress `597DAD88DDE56494EA55ADC51303793C5D302BE90B41B4BFF0AA0CAD0598C2B4`; C01 Completion `05EF90943AD6024C0FFFBB08C9D3993FECAA6DC0143065F986F59413C4EDE6ED`.
- 적용: `/bff/api` Browser Cookie·CSRF 불변, `/api/v1` exact Native Bearer 전용, login/refresh만 무자격 exchange, Cookie/Set-Cookie·민감값 반사 fail-close, redirect/timeout/body 경계 보존.
- 허용 제품/Test는 정확히 3개이며 실제 Credential/network/deploy/container/DB/migration/commit/push/PR은 0으로 유지한다.
- 다음 작업: 승인 3개 파일 현재 구현을 EOF까지 검토하고 Native 채널 계약 RED 테스트를 먼저 추가한다.

## 2026-08-12T10:55:00+09:00 · Native 채널 TDD RED

- 상태: `RED_CONFIRMED`
- 변경 파일: `scripts/tests/api-bff-runtime.test.mjs`만
- 실행: `node --test scripts/tests/api-bff-runtime.test.mjs`
- 결과: 19개 중 기존 Browser 15 PASS / 신규 Native 4 FAIL, exit 1.
- 정확한 실패: `createNativeBffProxy` export 3건 `undefined !== function`; 실제 public `/api/v1/auth/native/login` handler `404 !== 422`.
- 의미: 기존 Browser Cookie·CSRF 회귀는 보존됐고, 승인 Native 전용 proxy와 handler 분리가 없음을 재현했다.
- 다음 작업: exact Native route/Bearer/Cookie/response 경계와 `/api/v1` handler 전환 최소 구현.

## 2026-08-12T11:02:00+09:00 · Native 채널 GREEN·보안 보강

- 상태: `GREEN_CONFIRMED`
- 변경 파일: 승인 제품/Test 정확히 3개.
- 구현: `createNativeBffProxy` exact login/refresh·Session·Workspace 7종·Recovery 7종 분류, protected 단일 Bearer, 모든 Native Cookie 차단, request/response 상한, redirect·Set-Cookie·Content-Type·length·민감값/내부 origin 반사 fail-close.
- `/api/v1` Route만 Native proxy로 전환하고 GET/POST만 export; `/bff/api` Browser `createBffProxy`는 변경하지 않았다.
- 오류/복구 1: URLSearchParams 닫는 괄호 누락으로 suite syntax error. 한 글자 보정 후 기능 테스트로 진입.
- 오류/복구 2: Citation test upstream이 JSON을 반환해 expected PDF gate에서 502. 설계 계약에 맞게 test fixture만 PDF로 보정.
- 추가 RED: upstream allowlist Header가 Bearer를 반사할 때 `200 !== 502`를 재현. Header와 PDF/JSON body 반사 검사를 추가해 GREEN.
- 최종 집중 테스트: 19 PASS / 0 FAIL / exit 0. 실제 Route Handler 동등 실행에서 login 422 non-404, Session 401, Cookie 400/upstream 0.
- 다음 작업: Work Order의 API Runtime·API 전체·OpenAPI·Web Build·Product Gate·Diff/Dirty 보존 전체 검증.

## 2026-08-12T11:12:08+09:00 · Actual Probe RED·승인 갱신·복구

- 상태: `ACTUAL_PROBE_GREEN`
- 첫 `npm run verify:api-runtime`은 기존 `services/api/.venv`의 `httpx._transports` 누락으로 제품 assertion 전 exit 1. 기존 환경을 수정하지 않고 task 전용 `UV_PROJECT_ENVIRONMENT=C:\Users\cyhuh\AppData\Local\Temp\daon-task8-c02-api-runtime-20260812`를 사용했다.
- 격리 재실행은 API runtime 23/23, lifecycle Windows 2/2(POSIX skip 4), BFF 19/19, Web Build, Product 257/0까지 통과했으나 actual Next `/bff/api/session`이 `503 GATEWAY_CONFIGURATION_INVALID`, upstream trace 0으로 RED였다.
- 원인: `runtime_process_probe.start_next()`가 기존 Browser BFF의 필수 `DAON_PUBLIC_GATEWAY_URL`을 누락했다. 임시 process env에 공개 HTTPS origin을 준 focused 실제 GET은 200/data/trace 일치로 Browser 제품 경로 회귀 0을 입증했다.
- 어울1 승인 갱신: 계획 SHA-256 `90A03B1D50F68FC228AE4518EB874F9A8EE197E8EBF12B02078DBB0F743773C4`; C02 Work Order SHA-256 `5A3A29352268A28B6097BDB9294F25DEE4D2FF29585B4743CF7820C370B0DC44`. 허용 Test에 `services/api/tests/runtime_process_probe.py`가 추가됐다.
- 최소 복구: 내부 Next listener/request는 HTTP localhost로 유지하고 process env에 `DAON_PUBLIC_GATEWAY_URL=https://localhost:<web_port>`를 주입했다. same-origin write의 `Origin`만 같은 공개 HTTPS origin으로 분리했다. 제품 BFF의 HTTPS 강제나 localhost 예외는 변경하지 않았다.
- 복구 후 동일 Gate exit 0: actual API/Next true, Browser GET 200·direct 의미 동일, write 200, cross-origin 403/upstream audit 0, credential/internal hit 0, process/listener 잔여 0.

## 2026-08-12T11:12:08+09:00 · 필수 검증·보존 완료

- 상태: `COMPLETED_PENDING_REVIEW`
- `node --test scripts/tests/api-bff-runtime.test.mjs`: 19 PASS / 0 FAIL.
- `npm run verify:api-runtime`: exit 0; runtime 23/23, lifecycle Windows 2/2(POSIX skip 4), BFF 19/19, clean Web Build, target Web Product 257/0, actual API/Next PASS.
- Work Order exact root pytest는 폐기 OneDrive 경로 혼입과 project dependency 미선택으로 79 collection errors였다. 공식 `services/api`, `PYTHONPATH=src`, task 전용 격리 env의 동등 전체 실행은 `306 passed, 25 skipped, 27 warnings, 134 subtests passed`.
- `node scripts/verify-openapi-contract.mjs`: paths 70 / operations 96 / schemas 104 / errors 31 / SHA-256 `A229ECD726855E4E838888E7F4E369623ED40255173FDAA99CB9BC618F3F7857`.
- Web Build: Next production build exit 0, `/api/v1/[...path]`와 `/bff/api/[...path]` dynamic route 생성 확인. `npm run verify:product-ui-boundary`: scanned 269 / violations 0 / boundaryErrors 0.
- `git diff --check`: exit 0(기존 line-ending warning만). 승인 변경은 제품/Test 4개와 C02 문서뿐이다.
- 보안/보존: 변경 제품의 `NEXT_PUBLIC_API_BASE_URL`, `http://localhost`, `http://127.0.0.1`, `api:8000` literal 각 0; staged 0; 기존 삭제 25; Cargo worktree/HEAD blob 모두 `bbf68886c6a96f9201994714be5dc13b8275d855`; Native Evidence·기존 미추적 문서 보존.
- 실제 Credential/network/deploy/container/DB/migration/commit/push/PR은 0. 다음 작업은 독립 검토 및 별도 Commit·Push·PR 승인 판단이다.

## 2026-08-12T11:49:58+09:00 · 독립 검토 NEEDS_CHANGES 인수

- 상태: `REWORK_IN_PROGRESS`
- 독립 검토: Critical 0 / Important 4. 동일 Issue `R1-USER-PRODUCT-SEPARATION-TASK8-01-I001`로 C02를 재개했다.
- 갱신 승인 문서 SHA-256: Spec `624B9DD50E046F6A60C45CD7793DEF7E6103282905F91D767D6E06C29BDD15B0`; Plan `3464D43744942EB9DC466F71B9D0830331FE7B15774EAA114489A6E0A9E658A6`; C02 WO `21EB75C58413F0A37C4AE339D09D4D7DEA91FB7D3343F7770AF46949E9AC0B3C`.
- 추가 허용 5개: Web Notification/Inbox·Recovery Adapter, 관련 2개 Node test, actual runtime probe. 제품 BFF HTTPS 강제, Browser Cookie/CSRF 동작과 DB·API·OpenAPI는 변경 금지 유지.

## 2026-08-12T11:49:58+09:00 · Important 1 Adapter RED→GREEN

- RED: 실제 Adapter 실행 테스트에서 Notification 2 PASS / 1 FAIL, Recovery 6 PASS / 1 FAIL. 모든 기대 `/bff/api`가 실제 `/api/v1`로 관찰됐다.
- 최소 변경: `notification-inbox-api.js`, `recovery-api.js`의 same-origin prefix만 `/bff/api`로 이관했다. DTO·Method·query·Content-Type·If-Match·Idempotency-Key·body·`credentials: same-origin` 의미는 보존했다.
- GREEN: Notification 3/3, Recovery 7/7. 실제 Adapter 실행 URL의 `/api/v1` direct 0.

## 2026-08-12T11:49:58+09:00 · Important 2~4 Native framing RED→GREEN

- RED: BFF suite 19 PASS / 2 FAIL. Recovery 200KiB JSON이 기존 128KiB 상한으로 502였고 정상 JSON 공개 응답의 계산 Content-Length가 null이었다.
- 최소 변경: Recovery 7종만 1MiB, 다른 JSON 128KiB, Citation PDF 25MiB를 적용했다. request/response media type은 parameter 제거 후 exact JSON/PDF로 비교하고, credential-exchange sensitive body 수집은 Content-Type 분기 밖에서 수행한다. buffer 검증 후 계산 Content-Length를 설정하며 Transfer-Encoding은 allowlist에 넣지 않았다.
- GREEN: BFF 21/21. Recovery 200KiB 허용·1MiB 초과 거부, 일반 JSON 128KiB 초과 거부, `application/json; charset=utf-8`와 PDF parameter 허용, 유사 media 거부, credential 반사 502, Content-Length 일치·TE 부재.

## 2026-08-12T11:49:58+09:00 · Actual Next 확장 RED→GREEN

- RED 1: actual Next는 empty login 400(non-404), unauth Session 401, Cookie 400, unknown 404, Notification/Inbox 200이었으나 Web Cookie session token을 Native Bearer로 재사용해 authorized Session/Recovery가 401이었다. 채널 분리는 정상이고 probe fixture가 잘못됐다.
- 복구: 임시 SQLite의 결정적 Test Identity를 public `/api/v1/auth/native/login`으로 로그인하고 Access는 메모리 수명 내에서만 사용했다. Test Password·Access는 log/summary/Evidence 0 검사를 적용했다. 운영 Credential과 영속 DB는 사용하지 않았다.
- RED 2: Native Session 200으로 복구됐으나 Cloud dependency 없는 임시 runtime Recovery는 503이었다. Citation은 Postgres repository 전용이라 DB fixture 생성은 승인 범위와 충돌했다.
- 승인 대안: probe가 소유·정리하는 bounded loopback fake upstream과 actual Next 2차 process를 사용했다. fixed GET path와 Bearer 존재만 허용하고 다른 요청은 404/405 fail-close하며 credential 값은 저장·출력하지 않는다.
- GREEN: actual API/Next에서 login 200, empty login non-404, Session 401/200, Cookie 400, unknown 404, Cookie/unknown upstream audit 0, Notification/Inbox 200. 2차 Next에서 Recovery 200KiB JSON과 Citation PDF exact media/body length, Content-Length 일치, Transfer-Encoding 부재. 모든 process/listener 0.

## 2026-08-12T11:49:58+09:00 · Rework 전체 Fresh Gate

- 상태: `COMPLETED_PENDING_REVIEW`
- 집중: BFF 21/21, Notification 3/3, Recovery 7/7.
- `npm run verify:api-runtime`: exit 0; API runtime 23/23, lifecycle Windows 2/2(POSIX skip 4), actual API/Next 및 2차 framing PASS, clean Web Build, target Product 257/0.
- API 전체 공식 project 동등 격리: 306 passed, 25 skipped, 27 warnings, 134 subtests passed. exact root 명령은 기존 폐기 OneDrive sys.path·project dependency 미선택 환경 문제로 분리 유지한다.
- OpenAPI: paths 70 / operations 96 / schemas 104 / errors 31 / SHA-256 `A229ECD726855E4E838888E7F4E369623ED40255173FDAA99CB9BC618F3F7857`.
- 명시 Web production Build PASS, target Product 257/0, 전체 Product 269/0, `git diff --check` exit 0.
- 보안·보존: Web Adapter `/api/v1` direct 0; 승인 제품 4파일에서 내부/public API 주소 literal 0; staged 0; 삭제 25; Cargo worktree/HEAD blob `bbf68886c6a96f9201994714be5dc13b8275d855` 동일; Native Evidence·기존 미추적 보존.
- 변경은 승인 제품/Test 8개와 C02 Progress/Completion뿐이다. 운영 Credential·외부 network·deploy·container·영속 DB·migration·commit·push·PR 0. 승인된 Test Credential과 loopback은 probe 수명 안에서만 생성·정리했다.

# R1 사용자 제품 분리 Task 8 C02 완료 보고서

## 판정

`COMPLETED_PENDING_REVIEW`

- Issue ID: `R1-USER-PRODUCT-SEPARATION-TASK8-01-I001`
- 독립 검토 재작업: Critical 0 / Important 4 전부 보정
- 공식 정본: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`
- Branch / HEAD: `codex/task8-clean-build-c01` / `ac8544762e5a97e286694c3631cf5d4f507790c4`
- 최신 승인 SHA-256: Spec `624B9DD50E046F6A60C45CD7793DEF7E6103282905F91D767D6E06C29BDD15B0`; Plan `3464D43744942EB9DC466F71B9D0830331FE7B15774EAA114489A6E0A9E658A6`; C02 WO `21EB75C58413F0A37C4AE339D09D4D7DEA91FB7D3343F7770AF46949E9AC0B3C`.

## 판단 이유

1. Web Notification·Inbox·Recovery Adapter는 실제 실행 테스트에서 `/bff/api`만 호출하며 `/api/v1` direct는 0이다. 기존 DTO·Method·query·header·body·same-origin credential 의미를 보존했다.
2. Native Recovery 7종 JSON은 1MiB, 다른 Native JSON은 128KiB, Citation PDF는 25MiB 상한이다.
3. Native request/response media type은 parameter 제거 뒤 `application/json` 또는 `application/pdf`와 exact 비교한다. credential exchange 민감값 수집은 inbound Content-Type에 의존하지 않는다.
4. 검증된 buffer로 공개 Content-Length를 계산하며 Transfer-Encoding은 전달하지 않는다. Unit과 actual Next JSON/PDF에서 길이·media·TE 경계를 확인했다.
5. `/bff/api` Browser Cookie·same-origin CSRF는 유지하고 `/api/v1`만 exact Native Bearer proxy로 분리했다. 제품 HTTPS 경계는 완화하지 않았다.

## 변경 결과

- 제품: `apps/web/lib/bff-api-proxy.js`, `apps/web/app/api/v1/[...path]/route.js`, `apps/web/lib/notification-inbox-api.js`, `apps/web/lib/recovery-api.js`.
- Test: `scripts/tests/api-bff-runtime.test.mjs`, `scripts/tests/notification-inbox-ui.test.mjs`, `scripts/tests/recovery-api.test.mjs`, `services/api/tests/runtime_process_probe.py`.
- 문서: C02 Progress와 본 Completion.
- actual probe는 임시 SQLite 결정적 Test Identity와 메모리 수명 Native Access만 사용한다. Cloud/PDF framing은 probe 소유 bounded loopback upstream과 실제 Next 2차 process로 검증하며 fixed GET path 외 요청은 fail-close한다.

## TDD 근거

- Adapter RED: Notification 2/3, Recovery 6/7. 실제 `/api/v1` direct 호출을 관찰했다. GREEN: Notification 3/3, Recovery 7/7, direct 0.
- Framing RED: BFF 19/21. Recovery 200KiB가 502이고 Content-Length가 null이었다. GREEN: BFF 21/21.
- Actual RED: Web token의 Native 재사용 401과 cloud-less Recovery 503으로 fixture 경계를 확인했다. GREEN: public Native login 200, Session 401/200, Cookie 400, unknown 404, rejected upstream audit 0, Notification/Inbox 200, bounded Recovery/PDF framing PASS.

## Fresh 테스트 결과

- `node --test scripts/tests/api-bff-runtime.test.mjs`: 21/21 PASS.
- Notification/Inbox: 3/3 PASS. Recovery Adapter: 7/7 PASS.
- `npm run verify:api-runtime`: exit 0; API runtime 23/23, lifecycle Windows 2/2(POSIX skip 4), actual API/Next true, clean Web Build, target Product 257/0, process/listener 0.
- API 전체 공식 project 동등 격리: 306 passed, 25 skipped, 27 warnings, 134 subtests passed.
- OpenAPI: 70 paths, 96 operations, 104 schemas, 31 errors, SHA-256 `A229ECD726855E4E838888E7F4E369623ED40255173FDAA99CB9BC618F3F7857`.
- 명시 Web production Build PASS; Product target 257/0, 전체 269/0.
- `git diff --check`: exit 0.

## 보안·보존·미해결

- Web Adapter `/api/v1` direct 0. 승인 제품에서 `NEXT_PUBLIC_API_BASE_URL`, localhost, loopback, Docker API literal 0.
- Test Password와 Native Access는 log/summary/Evidence 0이며 process와 listener도 0이다. 실제 운영 Password·Credential·network·deploy·container·영속 DB·migration은 사용·변경하지 않았다.
- staged 0, 기존 삭제 25, Cargo worktree/HEAD blob `bbf68886c6a96f9201994714be5dc13b8275d855` 동일, Native Evidence와 기존 미추적 문서 보존.
- exact root pytest의 기존 79 collection errors는 폐기 OneDrive sys.path와 project dependency 미선택 환경 문제이며, 공식 API project의 fresh 동등 격리 전체 PASS로 분리했다.
- 구현 미해결은 없다. 독립 재검토와 별도 Commit·Push·PR, Task 8/D01 재개는 어울1 판단 전 금지한다.

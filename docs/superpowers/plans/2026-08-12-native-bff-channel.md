# Native BFF Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 공개 `/api/v1`에 Cookie와 분리된 exact Native Bearer BFF 채널을 제공해 Native Login과 승인 Native API를 안전하게 API 컨테이너로 전달한다.

**Architecture:** 기존 `/bff/api`는 `createBffProxy` Browser 계약을 유지한다. `/api/v1` Route만 `createNativeBffProxy`를 사용하며, exact Route·Method, Credential Exchange 예외, Bearer, Cookie/Set-Cookie 차단을 독립 적용한다.

**Tech Stack:** Next.js Route Handler, Web Fetch API, Node.js test runner, 기존 `bff-api-proxy.js` Safe Error/timeout/body helpers.

## Global Constraints

- Browser same-origin BFF와 Cookie·CSRF 동작은 변경하지 않는다.
- Native Login·Refresh만 무인증이며 나머지 exact Native Route는 Bearer 필수다.
- Cookie·Set-Cookie·Credential 원문·내부 URL은 Native 채널에 노출하지 않는다.
- API·OpenAPI·Rust·Nginx·Compose·DB·Migration·의존성은 변경하지 않는다.
- 실제 Credential·배포·Container 변경은 본 구현에서 금지한다.

---

### Task 1: Native Route·Credential 경계 TDD

**Files:**
- Modify: `apps/web/lib/bff-api-proxy.js`
- Modify: `scripts/tests/api-bff-runtime.test.mjs`

**Interfaces:**
- Produces: `createNativeBffProxy({ baseUrl, publicOrigin, fetchImpl, timeoutMs })`
- Consumes: 기존 Safe Error, Trace, abort, body/response 경계 helper.

- [ ] 실패 테스트를 추가한다: Native login/refresh exact POST, Session GET와 Workspace/Recovery exact Route, unknown/method mismatch, Cookie, missing/malformed Bearer.
- [ ] `node --test scripts/tests/api-bff-runtime.test.mjs`를 실행해 현재 404·export 부재 RED를 확인한다.
- [ ] Native Route classifier와 Bearer validator를 최소 구현한다. Login/Refresh에는 Authorization을 제거하고 보호 Route에는 검증된 단일 Bearer만 전달한다.
- [ ] Native 응답에서 Set-Cookie·redirect·내부 Header를 fail-close하고 Credential 원문 비반사를 고정한다.
- [ ] 같은 테스트를 실행해 Native 계약과 기존 Browser BFF 회귀를 GREEN으로 만든다.

### Task 2: `/api/v1` Route Handler 분리 TDD

**Files:**
- Modify: `apps/web/app/api/v1/[...path]/route.js`
- Modify: `scripts/tests/api-bff-runtime.test.mjs`

**Interfaces:**
- Consumes: `createNativeBffProxy`
- Produces: GET/POST Native Route Handler; Browser `/bff/api` handler 불변.

- [ ] Route source/실행 테스트에서 `/api/v1`이 기존 Browser Proxy를 사용하는 현재 상태를 RED로 고정한다.
- [ ] `/api/v1` handler만 `createNativeBffProxy`로 전환하고 public/internal origin parsing과 Safe configuration error를 유지한다.
- [ ] Login 빈 JSON, Session 무자격, valid Bearer protected request, Cookie request를 실제 Route handler 경계에서 검증한다.
- [ ] `node --test scripts/tests/api-bff-runtime.test.mjs`를 다시 실행해 GREEN을 확인한다.

### Task 3: Web Product Adapter Browser 채널 보존

**Files:**
- Modify: `apps/web/lib/notification-inbox-api.js`
- Modify: `apps/web/lib/recovery-api.js`
- Modify: `scripts/tests/notification-inbox-ui.test.mjs`
- Modify: `scripts/tests/recovery-api.test.mjs`

**Interfaces:**
- Consumes: 기존 `/bff/api` Browser Cookie·CSRF proxy.
- Produces: Notification·Inbox·Recovery Web Adapter의 same-origin Browser 경로.

- [ ] 실제 Product Adapter의 `/api/v1` 직접 호출을 실행 기반 RED로 고정한다.
- [ ] 두 Adapter의 prefix만 `/bff/api`로 이관하고 DTO·method·query·header·body·credentials 의미를 보존한다.
- [ ] Notification list/inbox/read와 Recovery Session/7 operations가 `/bff/api`만 호출하고 `/api/v1` 직접 호출 0임을 검증한다.
- [ ] 관련 Node 테스트와 Browser BFF 회귀를 GREEN으로 만든다.

### Task 4: Native framing·Production Build·회귀·보고

**Files:**
- Modify: `services/api/tests/runtime_process_probe.py`
- Create/append: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-TASK8-C02_progress.md`
- Create: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-TASK8-C02_completion_report.md`

**Interfaces:**
- Consumes: Task 1·2의 Native BFF.
- Produces: Task 8/D01 재개 판단 근거.

- [ ] `npm run verify:api-runtime`과 관련 BFF test를 실행한다.
- [ ] Actual Next Probe는 내부 Next process의 HTTP 주소와 별개로 `DAON_PUBLIC_GATEWAY_URL=https://localhost:<web_port>`를 설정하고, same-origin write Header도 같은 공개 HTTPS Origin을 사용해 Reverse Proxy TLS termination을 재현한다. 설정 부재 503과 cross-origin 403을 RED로 확인한 뒤 최소 보정한다.
- [ ] 실제 Next에서 Native login non-404, Session401, Cookie/unknown upstream0, exact JSON/PDF framing, Recovery Content-Length 존재·Transfer-Encoding 부재를 검증한다.
- [ ] API 전체와 OpenAPI verifier를 실행해 공개 API 계약 불변을 확인한다.
- [ ] `npm run build --workspace @daon-user/web`과 `npm run verify:product-ui-boundary`를 실행한다.
- [ ] `git diff --check`, sensitive/internal URL scan, 허용 파일·삭제25·Cargo/Native Evidence 보존을 확인한다.
- [ ] `COMPLETED` 결과 계약을 제출하고 Commit·Push·PR·배포는 어울1의 별도 판단 전 수행하지 않는다.

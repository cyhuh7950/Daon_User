# Native 전용 BFF 채널 보정 설계

## 1. 배경과 결정

Task 8 배포에서 API 컨테이너의 `POST /api/v1/auth/native/login`은 등록되어 있었지만, 공개 `/api/v1/...`를 처리하는 Web BFF가 Browser 인증 Route만 허용하여 404를 반환했다. 신산님은 2026-08-12에 Browser와 Native 인증 채널의 명시적 분리를 승인했다.

검토한 대안은 다음과 같다.

1. 공용 Reverse Proxy가 `/api/v1`을 API 컨테이너로 직접 전달: 단순하지만 Web BFF의 Route allowlist·body 상한·redirect·Safe Error 경계를 우회한다.
2. 기존 Browser BFF에 Native Route를 혼합: 변경량은 작지만 Cookie·CSRF와 Bearer·Credential Exchange가 한 함수에 섞인다.
3. **선택안 — 같은 Web Server 안에 Native 전용 BFF 실행 경계를 분리**: `/bff/api`의 Browser Cookie 계약은 보존하고 `/api/v1`은 exact Native allowlist와 Bearer 계약만 적용한다.

## 2. 채널 경계

### Browser 채널

- 공개 경로: `/bff/api/...`
- 기존 `createBffProxy`를 그대로 사용한다.
- `__Host-daon_session` 단일 Cookie와 same-origin CSRF 검증을 유지한다.
- Browser source·동작·응답 계약을 변경하지 않는다.
- 기존 Web Product Adapter는 모두 `/bff/api`를 사용한다. 현재 `/api/v1`을 직접 사용하는 Notification·Inbox와 Recovery Adapter는 요청 의미를 유지한 채 Browser 채널로 이관한다.

### Native 채널

- 공개 경로: `/api/v1/...`
- `apps/web/app/api/v1/[...path]/route.js`만 Native 전용 Proxy를 사용한다.
- Cookie가 존재하면 upstream 호출 0으로 거부한다. Cookie를 Bearer로 변환하거나 혼합하지 않는다.
- upstream `Set-Cookie`는 Native 응답 계약 위반으로 fail-close하고 사용자에게 전달하지 않는다.
- 내부 목적지는 기존 `DAON_API_INTERNAL_URL`의 fixed origin만 사용하고 redirect는 `manual`로 유지한다.
- Client가 보낸 Host·Forwarded·Tenant·Role·내부 주소 Header는 전달하지 않는다.

## 3. 정확한 Route·인증 계약

무인증 Credential Exchange는 다음 두 POST만 허용한다.

- `POST /api/v1/auth/native/login`
- `POST /api/v1/session/refresh`

두 Route는 inbound Authorization과 Cookie를 전달하지 않는다. Body는 기존 JSON 상한 65,536 bytes를 적용한다.

다음 Route는 정확한 `Authorization: Bearer <opaque>`가 있어야 하며 Cookie는 금지한다.

- `GET /api/v1/session`
- Workspace 7종: Source list/upload, processing status, question, citation content, Studio report create/output list
- Cloud Recovery 7종: backup create/list/get, restore preview/get/execute/cancel

허용 Method·Path는 Rust `NativeWorkspaceOperation`과 `CloudOperation`의 현재 계약과 일치해야 한다. 임의 부분 경로, query, 추가 segment, 다른 Method는 upstream 호출 0으로 404/405 Safe Error를 반환한다. Upload만 25MiB 요청 상한을 사용하고 나머지는 65,536 bytes다. Recovery 7종 JSON 응답 상한은 Rust Client와 동일한 1MiB, 다른 Native JSON은 128KiB, Citation PDF는 25MiB다.

Bearer는 `Bearer ` 접두사 하나, 16~4,096 bytes, CR/LF·C0·공백·쉼표 없는 opaque 값만 허용한다. 값은 로그·오류·Evidence에 남기지 않고 upstream `Authorization` Header 소유자에게만 전달한다. 누락·중복·형식 오류는 401 `AUTHENTICATION_REQUIRED`, 민감 원문은 응답에 포함하지 않는다.

## 4. 오류·응답 경계

- 기존 timeout, client abort, request body 상한, response body 상한, Content-Length·chunked 검증, redirect 차단과 Safe Trace를 재사용한다.
- Native Channel은 Cookie/Set-Cookie, Location, Server, 내부 URL과 Credential 원문을 응답으로 반사하지 않는다.
- upstream이 redirect, malformed length, oversized body, Set-Cookie 또는 비계약 Content-Type을 반환하면 `GATEWAY_RESPONSE_REJECTED` 계열 Safe Error로 닫는다. Media type은 parameter를 제거한 뒤 `application/json` 또는 `application/pdf`와 exact 비교한다.
- Body를 검증·버퍼링한 뒤 공개 응답에 계산한 정확한 `Content-Length`를 설정하고 `Transfer-Encoding`은 전달하지 않는다.
- Body가 있는 Native 요청의 Content-Type도 Route별 exact JSON/PDF로 검증한다. Credential Exchange 민감값 수집은 inbound Content-Type의 정당성에 의존하지 않는다.
- 로그인 실패·인증 실패·권한 실패의 API Safe status와 body는 민감 Header 제거 후 보존한다.

## 5. 테스트와 완료 조건

- TDD RED에서 현재 `/api/v1/auth/native/login` 404를 재현한다.
- Browser `/bff/api` Cookie·CSRF 회귀를 그대로 통과시킨다.
- Native login/refresh는 Cookie·Authorization 전달 0, exact path/body만 전달한다.
- 보호 Route는 valid Bearer 1회 전달, missing/malformed Bearer·Cookie·unknown path에서 upstream 호출 0을 검증한다.
- 실제 Next production process에서 공개 `/api/v1/auth/native/login` 빈 JSON이 404가 아닌 승인 입력 오류를 반환하고, `/api/v1/session` 무자격 요청은 401이어야 한다.
- 실제 Next production process에서 Cookie·unknown Native Route upstream 0, JSON/PDF framing, Recovery Content-Length 존재·Transfer-Encoding 부재와 Web Notification·Inbox Browser 경로를 검증한다.
- API 전체, BFF 집중 테스트, Web clean Build/Product Gate와 secret/internal URL scan을 통과해야 한다.
- 실제 Password·Credential·로그인 시도, 배포, DB·Migration, Container 재시작은 별도 Task 8/D01 재개 단계에서만 수행한다.

## 6. 범위

- 제품 변경은 Web BFF Proxy, `/api/v1` Route, Web Notification/Recovery Adapter와 관련 BFF·Adapter·actual probe 테스트로 제한한다.
- API Runtime·OpenAPI·Rust·Nginx/공용 Proxy·Compose·DB·Migration·의존성은 변경하지 않는다.
- 기존 사용자 삭제 25건, Cargo 동일 Blob, Native Evidence와 기존 미추적 문서를 보존한다.

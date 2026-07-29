# R1-M4-05 BFF·Gateway·FastAPI 실행 경계 작업지시서

## 승인 기준과 Writer

- Work Order ID: `R1-M4-05`.
- Branch `codex/r1-m4-05`, 기준 `codex/release-1` Merge SHA `7bc082d652e38d40d1dc88e41b628b10b1f3b311`, 시작 Clean.
- 상세 설계 v0.7 §14·§15·§17, 구현계획 v0.9 M4-05, 테스트계획 v0.7, R1-M4-01·03·04 정본을 적용한다.
- 어울2가 이 Worktree와 작업 범위의 유일한 Writer다. PR·CI·Merge는 어울1 소유다.

## 단일 목표

M4-01 OpenAPI와 M4-03 Identity, M4-04 Authorization을 실제 FastAPI Process에 결합하고, Web은 same-origin BFF, Native는 승인된 HTTPS Gateway 의미로만 접근하는 실행 경계를 구현한다. 실제 HTTP 요청·Trace 계보·안전 오류·Graceful Shutdown·동일 Port 재기동을 증명한다.

## 허용·제외 범위

- 허용: `services/api` FastAPI App/Runtime/설정·테스트, `apps/web/app/bff/api/**` Server Route Handler와 서버 전용 Helper, 정확히 필요한 API dependency manifest/`uv.lock`, OpenAPI의 M4-05 경계 정합, Runtime/BFF verifier·evidence, Architecture·README, 본 작업 문서.
- 승인 Pin: 기존 Lockfile과 Local Service 기준의 `fastapi==0.139.2`, `uvicorn==0.51.0`; 테스트 도구도 이미 승인된 정확 Pin만 재사용한다.
- 제외: Browser 화면 기능 변경, Local Service·Tauri Loopback 변경, PostgreSQL/RLS/Migration, Docker/ysna 배포, 실제 외부 OIDC Provider, M5 이후 Domain Route 전체 구현, 임시 Mock 성공 응답, `NEXT_PUBLIC_API_BASE_URL`.
- 기존 M4-02 Audit, M4-03 credential/Step-up, M4-04 역할 범위·AccessDecision 계약과 기존 테스트를 보존한다.

## FastAPI 실행 경계

1. App Factory와 Process Entrypoint를 분리하고 설정·Identity·Authorization·Audit dependency를 명시적으로 주입한다. Import 시 Listener·DB·Thread를 시작하지 않는다.
2. 최소 실제 Route는 `GET /health/live`, `GET /health/ready`, `GET /api/v1/session`, `POST /api/v1/workspaces/{id}/authorization/evaluations`, `POST /api/v1/access-decisions`, `GET /api/v1/audit-events`다. M4-01 Operation/Schema/Error 의미와 일치해야 하며 미구현 공개 Route를 성공처럼 응답하지 않는다.
3. 인증은 M4-03의 실제 Web Session 또는 Native Bearer 검증 결과인 `IdentityPrincipal`만 사용한다. Client가 보낸 tenant/user/role claim을 신뢰하지 않는다.
4. Workspace/Access/Audit Route는 M4-04 현재 Repository 역할·ACL·정책 및 M4-02 Audit Store를 실제 호출한다. 무권한 403, foreign/missing 404 비노출, 현재 권한 AccessDecision 의미를 유지한다.
5. Request body·header·content-type·method를 제한하고 malformed JSON, oversized request, 내부 예외를 안정적인 M4-01 Error Envelope로 변환한다. Stack Trace, DB/Provider/internal host, secret·token·digest를 응답·로그에 노출하지 않는다.
6. Live는 Process 생존만, Ready는 필수 dependency가 안전하게 요청을 받을 수 있을 때만 200이다. 종료 시작 후 Ready는 실패하고 신규 업무 요청을 거부한다.

## Web same-origin BFF 계약

- Browser 실행 코드는 오직 `/bff/api/...` 상대 경로를 호출한다. API 절대주소, `localhost`, `127.0.0.1`, Docker Host/Port, `NEXT_PUBLIC_*` 내부주소를 포함하지 않는다.
- Next Route Handler의 서버 실행 코드만 `DAON_API_INTERNAL_URL` 같은 server-only 설정을 읽는다. 허용 Scheme/Host/Port와 고정 Base Path를 시작 시 검증하고 요청값으로 목적지를 조합하지 않는다.
- BFF는 허용 Method·Path·Query·Header만 전달하고 Hop-by-hop Header, 임의 `Host`/`Forwarded`, Client tenant/role claim을 제거한다. Web의 HttpOnly Session 의미는 보존하되 refresh/access credential을 Browser JSON에 반사하지 않는다.
- Redirect를 자동 추종하지 않고 내부 응답의 `Location`, server banner와 내부 주소를 Browser에 노출하지 않는다. Timeout·연결 실패·취소를 안정 오류와 retryable 의미로 변환한다.
- 실제 Next Process와 API Process를 함께 띄운 검증에서 Browser 관점 요청 URL은 same-origin `/bff/api/...`이고, 내부 URL은 Client bundle/응답/로그에 0건이어야 한다.

## Native Gateway·Transport 계약

- Native 공개 Gateway는 운영에서 HTTPS만 허용한다. TLS 종료가 Reverse Proxy인 경우 신뢰된 Proxy 경계에서만 전달 Proto를 인정하고 Client가 직접 보낸 `X-Forwarded-*`를 신뢰하지 않는다.
- 개발 Runtime의 평문 HTTP 허용은 loopback·명시적 test/development profile에만 제한한다. 외부 Interface 평문 Bind는 시작 전에 fail-close한다.
- CORS는 기본 비허용이며 Browser의 API 직접 Cross-origin 호출을 허용하지 않는다. Native Bearer는 Authorization Header로만 받고 Query/Path/로그에 남기지 않는다.

## Trace·요청 수명주기

- 유효한 `traceparent` 또는 승인 형식 Trace ID만 계승하고, 없거나 잘못되면 Server가 새 opaque Trace ID를 생성한다. 응답 Header, Error Envelope, Audit Event에 동일 Trace 계보를 남긴다.
- BFF가 생성·계승한 Trace는 Gateway→Identity→Authorization→Audit까지 일치해야 한다. Client가 Trace를 이용해 다른 Tenant 정보나 내부 식별자를 주입할 수 없다.
- 모든 요청은 제한 시간과 취소 전파를 가지며 종료 중 신규 Write는 시작하지 않는다.

## Process·Graceful Shutdown 계약

- PID/Port/Secret을 source에 고정하지 않는다. Runtime 설정은 명시적이고 secret 값은 출력하지 않는다.
- SIGTERM/정상 종료에서 Ready를 먼저 내리고 신규 요청을 중지한 뒤 진행 중 요청을 bounded drain하고 DB/Repository를 닫아 exit 0으로 종료한다.
- 실제 Process를 종료한 직후 같은 loopback Port로 재기동해 Live/Ready와 대표 Auth 요청이 다시 성공해야 한다. 자식 Process·Listener·임시 credential을 남기지 않는다.

## TDD·필수 검증

- RED부터 고정: App import side-effect 0, live/ready, session cookie/native bearer, spoofed tenant/role 거부, 403/404 비노출, Step-up/Error Envelope, Trace 계보, body/header/method/content-type 한도, CORS/redirect/internal address 비노출, graceful shutdown/restart.
- FastAPI in-process 테스트만으로 완료하지 않는다. 실제 API Process Raw HTTP와 실제 Next Production Process BFF 요청을 검증한다.
- BFF verifier는 source와 production client bundle 양쪽에서 금지 주소·`NEXT_PUBLIC_API_BASE_URL` 0건, 실제 요청 URL same-origin을 확인한다.
- 기존 Authorization 22, Identity 18, Audit 13, OpenAPI, Web, Workspace, Independence, Toolchain과 관련 Quality capability를 실행한다. 테스트 기대 삭제·완화 금지.
- GUI/Browser를 열지 않는다. HTTP/Network 증거는 자동화된 Process·요청 캡처로 수집하며 실제 Production Browser 검증을 주장하지 않는다.

## 진행·완료보고

- `docs/04_test_reports/release_1/R1-M4-05_progress.md`에 착수, RED, GREEN, 오류·복구, Runtime/BFF, 회귀, 종료 직전을 즉시 기록한다.
- `docs/04_test_reports/release_1/R1-M4-05_completion_report.md`는 `판정 → 판단 이유 → 조치`, 변경 파일, 실제 URL/Status/Trace, 종료·재기동, 검증 수, 제외 범위, 위험을 기록한다.
- 완료 시 단일 Commit을 Push하고 Local/Remote SHA 일치와 Worktree Clean을 보고한다. PR·CI·Merge·배포는 수행하지 않는다.

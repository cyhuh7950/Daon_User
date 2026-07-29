# API Runtime·same-origin BFF 실행 경계

## 적용 범위

R1-M4-05는 M4-01 OpenAPI, M4-02 Audit, M4-03 Identity, M4-04 Authorization을 실제 FastAPI process에 결합한다. Web browser는 `/bff/api/...`만 호출하고, Windows·Android·iOS native client는 승인된 HTTPS gateway에서 동일한 API 의미를 사용한다. PostgreSQL·RLS·Migration·외부 OIDC 통신은 M5 이후 소유 범위다.

## 실행 흐름

1. `RuntimeSettings`가 profile, bind host, port, database path, public gateway와 trusted proxy를 listener 시작 전에 검증한다.
2. App factory는 주입된 Identity·Authorization·Audit dependency만 조합하며 import만으로 listener·DB·thread를 시작하지 않는다.
3. 요청 경계가 header·body 크기, method, JSON content type, credential transport와 trace를 검증한다. Route 실행은 제한 시간 안에서 완료되어야 하며 timeout은 실제 작업을 취소하고 안전한 504로 변환한다.
4. Web cookie 또는 native bearer를 M4-03이 검증해 만든 `IdentityPrincipal`만 M4-04에 전달한다. Client의 tenant·user·role header는 권위 자료로 사용하지 않는다.
5. M4-04의 현재 repository 역할·ACL·정책과 M4-02 append-only Audit store가 권한·과거 결과 접근을 결정한다.

## Client별 전송 경계

| Client | Browser/Client 요청 | Credential | 내부 목적지 소유 |
| --- | --- | --- | --- |
| Web | same-origin `/bff/api/...` | `__Host-daon_session` opaque cookie | Next server route만 `DAON_API_INTERNAL_URL` 사용 |
| Native | 승인된 public HTTPS gateway | Authorization header opaque bearer | Native runtime의 고정 public gateway 설정 |
| Test/Development | loopback HTTP만 | 위 의미와 동일 | 명시적 `test`·`development` profile |

Production은 HTTPS public gateway와 명시적 trusted proxy IP가 없으면 시작 설정을 거부한다. Browser bundle에는 내부 API 환경변수, 내부 origin, `NEXT_PUBLIC_API_BASE_URL`이 포함되지 않는다. BFF는 고정 route·method·query·header allowlist만 전달하고 redirect를 따르지 않는다.

## 실제 Route

- `GET /health/live`: process 생존만 표시한다.
- `GET /health/ready`: 신규 업무 요청 수용 가능 상태만 200이다.
- `GET /api/v1/session`: 실제 M4-03 session을 안전 projection으로 반환한다.
- `POST /api/v1/workspaces/{id}/authorization/evaluations`: 실제 현재 권한을 평가한다.
- `POST /api/v1/access-decisions`: 과거 결과를 현재 권한으로 재검증한다.
- `GET /api/v1/audit-events`: 현재 tenant·workspace 권한 확인 후 안전 Audit page를 반환한다.

미구현 M5 이후 route는 성공으로 위장하지 않는다.

## Trace·오류·종료

- 유효 W3C `traceparent`의 trace ID를 우선 계승하고, 안전한 `X-Trace-Id` 또는 새 opaque trace를 사용한다.
- API 응답 header, 성공 envelope, 오류 envelope, Audit event가 같은 trace 계보를 유지한다.
- 입력값, credential, DB path, 내부 host, stack trace는 오류 envelope와 evidence에 포함하지 않는다.
- 종료 신호 수신 즉시 Ready를 내리고 신규 업무 요청을 `SHUTTING_DOWN`으로 거부한 뒤 inflight 요청을 제한 시간 동안 drain한다.
- Daon Uvicorn entrypoint는 shutdown 완료 뒤 signal을 재발생시키지 않고 exit 0으로 정상 return한다. 검증기는 실제 child의 lifecycle, exit 0, listener 해제, 동일 port 재기동을 함께 판정한다.

## 검증

- `npm run verify:api-runtime -- --write`: Runtime/BFF 단위 계약, Next production build, 실제 API·Next process, same-port 재기동과 evidence 생성
- `npm run verify:api-runtime`: 저장된 evidence와 현재 실행 결과 정합 검증
- GUI browser를 열지 않으며 자동 Raw HTTP·same-origin request capture만 증거로 사용한다.

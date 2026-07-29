# R1-M4-05-C01 BFF Credential·CSRF·취소 전파 중대 보완 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-05-C01`.
- Branch `codex/r1-m4-05`, 기준 HEAD `9506c5d1f2a81e8c7e4c97428d1cfd79068e541d`, 시작 Clean.
- R1-M4-05 정본과 어울1 독립 보안검토 결론을 적용한다.
- 어울2가 이 Worktree와 범위의 유일한 Writer다. PR·CI·Merge는 어울1 소유다.

## 판정과 단일 목표

- 판정: `MAJOR_GAP / CORRECTION_REQUIRED`.
- 이유: BFF가 Browser Cookie 전체를 upstream에 전달하고 Cookie 인증 Write의 same-origin/CSRF 경계를 강제하지 않는다. 또한 Client abort가 upstream fetch와 body read에 결합되지 않으며 설정 오류 Trace가 고정값이다.
- 목표: Web Session credential을 최소 전달하고, Cookie 기반 Write를 same-origin에서만 허용하며, 요청 취소·timeout을 실제 upstream 작업에 전파하고 모든 오류를 요청별 안전 Trace로 반환한다.

## 허용·제외 범위

- 허용: BFF helper/Route Handler, BFF tests/verifier/evidence, OpenAPI 안전 오류 정합이 필요한 최소 변경, M4-05/C01 진행·완료보고.
- 제외: FastAPI/Identity/Authorization/Audit 동작 변경, Browser UI, 새 인증 방식·외부 dependency, 공개 Route 확대, 전체 구조 재작성.
- 기존 실제 API/Next Process, exit 0·same-port, Runtime 10, BFF 3, OpenAPI와 회귀 계약을 보존한다.

## Credential 최소 전달

- Browser의 `Cookie` Header를 그대로 upstream에 전달하지 않는다.
- 고정 이름 `__Host-daon_session` 하나만 파싱해 단일 Cookie Header로 재구성한다. 다른 Cookie, malformed duplicate, CR/LF, oversize 값은 전달하지 않거나 안전 거부한다.
- `Authorization`, tenant/user/role claim, refresh/access token query, client-supplied Host/Forwarded는 기존대로 전달하지 않는다.
- 응답·오류·로그·evidence에 Session 값과 다른 Cookie 값이 0건임을 증명한다.

## Cookie Write CSRF·same-origin

- BFF의 POST/PUT/PATCH/DELETE는 요청 `Origin`이 실제 요청 URL의 origin과 정확히 일치할 때만 허용한다. 값 부재·`null`·다중값·Scheme/Host/Port 불일치는 업무 시작 전에 fail-close한다.
- `Sec-Fetch-Site`가 있으면 `same-origin`만 허용한다. 이 Header만 단독 신뢰하지 않는다.
- GET/HEAD는 기존 조회 의미를 유지한다. Native Bearer는 BFF가 아닌 HTTPS Gateway를 사용한다.
- CSRF 거부는 upstream 호출 0건, 안정 오류, 고유 Trace, credential 비반사를 증명한다.

## Abort·Timeout 전파

- BFF fetch signal은 Client `request.signal`과 timeout signal을 함께 결합한다. Client disconnect/abort 시 upstream fetch와 response body read가 취소되어 계속 실행되지 않는다.
- request body read, upstream fetch, response body read 각각의 abort/timeout/예기치 않은 오류를 안전 분류한다. Next 기본 오류 Body/Stack을 반환하지 않는다.
- timeout은 기존 504 retryable 의미를 유지한다. Client 취소는 별도 안전 코드 또는 승인된 공통 취소 의미로 구분하고 OpenAPI와 정합한다.
- Abort 후 Timer·Listener를 남기지 않는다. 테스트가 종료된 뒤 pending handle을 만들지 않는다.

## Trace·오류

- 설정 오류와 Route Handler의 예기치 않은 오류도 요청마다 새 opaque Trace ID를 만들고 응답 Header·Envelope에 동일 값을 둔다. 고정 Trace ID를 사용하지 않는다.
- 외부 오류에 내부 base URL, upstream Location/Server, raw exception, credential을 포함하지 않는다.

## TDD·검증

- RED: 다른 Cookie가 upstream에 전달되지 않음, malformed/duplicate session fail-close, cross-origin·Origin 없음·null·port mismatch Write 0 upstream, valid same-origin Write, client abort가 upstream signal을 실제 abort, body read/response read abort 안전 처리, 설정/예기치 않은 오류별 고유 Trace.
- 기존 BFF 3개 기대를 삭제·완화하지 않는다.
- 실제 Next production process의 same-origin session/Write와 client bundle 금지값·응답/로그 credential 0건을 재검증한다.
- R1-M4-05 Runtime verifier, BFF, OpenAPI, Web/Workspace, Security/Independence 관련 검증을 실행한다. 제품 파일이 바뀐 뒤 이전 Gate 증거를 최종으로 재사용하지 않는다.

## 진행·보고

`docs/04_test_reports/release_1/R1-M4-05-C01_progress.md`에 RED, 구현, 오류·복구, 실제 Process, 회귀, 종료 직전을 기록한다. 완료보고 후 같은 Branch에 단일 보완 Commit을 Push하고 Local/Remote SHA·Clean을 보고한다.

# OpenAPI v1 공통 계약

## 목적과 정본

`packages/contracts/openapi/v1/openapi.json`은 Daon 사용자 프로그램 Release 1의 `/api/v1` 공개 HTTP·SSE 계약 정본이다. 설계서 §17.1의 전체 Path, 요청·응답 Envelope, 안전 오류, 동시성·멱등성 Header와 실행 이벤트를 플랫폼·언어 중립 형태로 고정한다.

계약은 Runtime 구현이 아니다. `R1-M4-03`과 `R1-M4-04`가 공통 보안·쓰기 규칙을 구현하고, `R1-M4-05`가 Service Runtime과 계약을 연결한다. `R1-M6`과 `R1-M8`은 각각 Workspace·지식과 Studio Payload의 상세 Domain Schema를 소유하되 이 공통 Envelope와 오류 규칙을 변경하지 않는다.

## 공통 규칙

- ID는 내부 DB 형식에 종속되지 않는 불투명 문자열이다.
- 목록 API는 `cursor`, `limit`과 필요한 `filter`·`search` Query를 명시한다.
- 성공 응답은 `trace_id` Header를 제공하고, Resource 응답은 `ETag`를 제공한다.
- `POST`는 `Idempotency-Key`, `PATCH`·`DELETE`는 `If-Match`를 요구한다.
- 오류 응답은 안전한 공통 Envelope와 허용된 상세 Schema만 노출하며 Stack, SQL, 내부 Host·Path·Secret을 포함하지 않는다.
- 실행 이벤트는 `text/event-stream`과 `Last-Event-ID` 재개 계약을 사용한다.

## 네트워크와 보안 경계

Browser Client는 `/api/v1/...` same-origin 상대 경로만 호출한다. OpenAPI 정본에는 환경별 Server URL이나 `localhost`, Docker 내부 Host·Port를 넣지 않는다. 내부 연결 주소와 Provider Credential은 BFF·Proxy·Service Runtime 내부에만 존재한다.

## 검증과 증거

`npm run verify:openapi-contract`는 정본을 읽기 전용으로 검증한다. Path 누락·추가, 중복 `operationId`, 공통 Header 누락, 안전 오류 위반, 절대 Server URL과 SSE 계약 누락을 fail-close로 거부한다. `--write`를 명시한 경우에만 `docs/03_evidence/release_1/R1-M4-01/openapi-contract-summary.json`을 갱신하며, 정규화된 JSON의 SHA-256으로 동일 입력의 결정성을 증명한다.

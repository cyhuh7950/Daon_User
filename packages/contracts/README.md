# Public Contracts Package Boundary

## 책임

공개 요청·응답·이벤트·안전 오류·Snapshot을 정의하는 플랫폼·언어 중립 Schema 원천을 소유한다.

## 허용 의존

다른 저장소 구성요소에 의존하지 않는 Leaf 원천이다.

## 금지 의존

App·Service·UI·Token 내부 Source, Provider SDK, Runtime 구현과 Secret을 포함하지 않는다.

## OpenAPI v1 정본

- 정본: `openapi/v1/openapi.json`
- Package export: `@daon-user/contracts/openapi/v1/openapi.json`
- 검증: 저장소 루트에서 `npm run verify:openapi-contract`
- 결정적 증거 갱신: `npm run verify:openapi-contract -- --write`

기본 검증은 파일을 변경하지 않는다. `--write`를 명시한 경우에만 정규화된 Contract SHA와 Path·Operation 집계를 Release 1 증거 파일에 기록한다.

Runtime 구현과 Service 강제 적용은 이 Package의 책임이 아니며 각각 후속 Work Order가 소유한다.

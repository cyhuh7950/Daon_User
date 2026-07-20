# Public Contracts Package Boundary

## 책임

공개 요청·응답·이벤트·안전 오류·Snapshot을 정의하는 플랫폼·언어 중립 Schema 원천을 소유한다.

## 허용 의존

다른 저장소 구성요소에 의존하지 않는 Leaf 원천이다.

## 금지 의존

App·Service·UI·Token 내부 Source, Provider SDK, Runtime 구현과 Secret을 포함하지 않는다.

## 후속 Build

Versioned OpenAPI와 공통 Contract 검증은 `R1-M4-01`이 소유한다.

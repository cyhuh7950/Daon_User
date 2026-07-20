# Public API Service Boundary

## 책임

FastAPI 기반 공개 API, AI Orchestrator, Cloud-side Adapter와 Cloud 데이터 접근 경계를 소유한다. Client별 UI는 소유하지 않는다.

## 허용 의존

- `packages/contracts`

## 금지 의존

App·Local Service·UI·Token 내부 Source를 Import하지 않는다. Daon 직접 의존은 표준 Connector Adapter 바깥에서 금지한다.

## 후속 Build

공개 Gateway·FastAPI 실행 경계는 `R1-M4-05`가 소유한다.

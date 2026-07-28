# Public API Service Boundary

## 책임

FastAPI 기반 공개 API, AI Orchestrator, Cloud-side Adapter와 Cloud 데이터 접근 경계를 소유한다. Client별 UI는 소유하지 않는다.

## 허용 의존

- `packages/contracts`

## 금지 의존

App·Local Service·UI·Token 내부 Source를 Import하지 않는다. Daon 직접 의존은 표준 Connector Adapter 바깥에서 금지한다.

## Audit Event Core

- 정본: `src/daon_user_api/audit.py`
- 검증: 저장소 루트의 `npm run verify:api-audit`
- 결정적 증거 갱신: `npm run verify:api-audit -- --write`

R1-M4-02는 불변 Event와 append-only·hash-chain·순수 조회 계약만 소유한다. HTTP·FastAPI·Auth·Tenant 권한 강제는 R1-M4-04·05, PostgreSQL 저장·보존·복구는 M5가 소유한다.

## 후속 Build

공개 Gateway·FastAPI 실행 경계는 `R1-M4-05`가 소유한다.

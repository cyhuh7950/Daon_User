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

## Identity Core

- 정본: `src/daon_user_api/identity.py`
- 검증: 저장소 루트의 `npm run verify:api-identity`
- 결정적 증거 갱신: `npm run verify:api-identity -- --write`

R1-M4-03/C01은 OIDC Authorization Code+PKCE 검증 프로토콜, opaque Web/Native Session, Native Refresh 회전·재사용 Family 철회, Step-up 결합 명시적 Session·Device 철회, Device 상태와 1회용 Step-up Authorization, SQLite 재시작 경계를 소유한다. access·refresh 거부와 Device trust 결과는 안전한 Audit action으로 기록하고 DB·Audit에는 Credential 원문을 저장하지 않는다.

실제 OIDC Provider 통신, Web Cookie의 HttpOnly·Secure·SameSite·CSRF 적용, HTTPS Route와 PostgreSQL/RLS는 후속 M4-05·M5 소유다. 따라서 Fake Provider 시험은 외부 IdP 로그인 성공 증거가 아니다.

## 후속 Build

공개 Gateway·FastAPI 실행 경계는 `R1-M4-05`가 소유한다.

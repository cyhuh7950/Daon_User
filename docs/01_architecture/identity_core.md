# Identity Core 경계

## 목적

R1-M4-03은 Web·Windows·Android·iOS가 공유하는 인증 의미를 HTTP Framework와 분리한다. OIDC 어댑터는 서명과 표준 Claim 검증을 완료한 `VerifiedOidcClaims`만 Core에 전달한다.

## 흐름

1. 정책의 issuer·client·audience·redirect를 exact match한다.
2. state·nonce·PKCE verifier를 CSPRNG로 발급하고 SQLite에는 SHA-256 digest 또는 S256 challenge만 저장한다.
3. Callback은 transaction 미사용·만료, state, client·redirect, PKCE, 검증 완료 Claims의 issuer·audience·nonce·exp를 다시 확인한다.
4. Web은 opaque Session 값만 반환해 M4-05 Cookie 계층에 전달한다. Native는 opaque access·refresh를 반환하고 refresh 회전 및 replay 시 family/session을 철회한다.
5. Device revoke는 연관 Session·Refresh family를 같은 DB transaction에서 철회하고 후속 Sync key 폐기 이벤트를 반환한다.
6. Step-up은 actor·session·device·tenant·action·target·policy에 결합된 짧은 1회용 값이다.

## 원자성과 한계

보안 상태 쓰기는 Audit append가 실패하면 SQLite transaction을 rollback한다. Audit Core는 M4-02의 in-memory 저장소이며, Audit과 IAM DB 간 Crash-atomic durable outbox는 M5 영속 계층에서 완성한다.

SQLite Adapter는 개발·계약 검증용 재시작 기준선이다. 운영 PostgreSQL Migration·RLS, HTTP Route, Web Cookie 속성, CSRF, 실제 IdP와 Device Sync-key 폐기는 이 작업의 완료 주장에 포함하지 않는다.

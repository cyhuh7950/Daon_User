# Authorization Core 계약

## 범위

R1-M4-04는 공개 API Runtime 이전의 도메인 권한 경계다. 현재 인증 주체는 M4-03의 `IdentityPrincipal`에서만 받고, 역할·Membership·ACL·조직/Workspace 정책의 현재 저장 상태를 함께 평가한다. Browser·FastAPI Route, PostgreSQL Migration·RLS는 각각 M4-05·M5 소유이며 이 Core의 완료 증거로 주장하지 않는다.

## 역할과 권한

- 역할은 `personal_owner`, `organization_admin`, `workspace_admin`, `editor`, `reviewer`, `approver`, `viewer` 7개로 고정한다.
- 권한은 외부 LLM, 인터넷 검색, Local/Internal LLM, Daon 지식, 파일 다운로드·공유, 생산 지식 등록, 데이터 영역 이동, 최종 승인·외부 전달 8개를 독립 평가한다.
- 조직 `deny` 또는 `lock`은 Workspace 정책으로 완화할 수 없다. 명시 Grant가 없으면 역할 기본값을 적용하고, 역할·Action Matrix 바깥은 기본 거부한다.
- 조직 정책과 Connector 관련 정책 변경은 M4-03의 `organization_security_or_connector_policy_change` Step-up Authorization을 실제 소비해야 한다.
- 역할·Membership·ACL·정책은 Version을 가지며 쓰기는 `expected_version`이 일치할 때만 성공한다.

## 과거 결과의 현재 권한 재검증

과거 결과 자체는 삭제하거나 다시 쓰지 않는다. 원래 사용한 Source Version·근거 Reference·Segment 의존 관계와 당시 정책 Version은 불변 Descriptor로 보존한다. 인용 보기, 원문 열기, Export, 전달, 생산 지식 등록, 재실행 시점마다 현재 Membership·ACL·Source 접근·정책을 다시 평가해 새 불변 `AccessDecision`을 만든다.

- `available`: 현재 권한으로 모든 근거를 사용할 수 있다.
- `partially_redacted`: 안전하게 분리 가능한 비허용 Reference와 그 Segment만 Mask한다.
- `access_blocked`: 결정적 근거가 비허용이거나 안전 분리가 불가능하거나 요청 Action 자체가 현재 권한에 없다.

재실행은 과거 실행 Snapshot을 재사용하지 않는다. 현재 Membership/ACL/정책 Version·데이터 영역·비용 한도·허용 Source Version으로 새 Request Snapshot을 만들며 원래 결과는 변경하지 않는다.

## 저장과 감사

현재 단계의 `SqliteAuthorizationRepository`는 주입된 DB Path에 `auth_*` Table만 생성한다. Identity와 같은 SQLite 파일을 주입할 수 있지만 Schema Namespace를 공유하지 않는다. 모든 SQL 값은 Parameter Binding을 사용하고 Tenant 조건을 Service 계층에서 강제한다. PostgreSQL RLS는 M5에서 별도 검증한다.

역할·정책·Source 접근 변경과 권한 허용·거부, 과거 결과 접근 결정, 재실행 승인은 M4-02 Audit Core에 안전한 Before/After·Count·Reason Code만 기록한다. Token, Step-up Authorization 원문과 Source 원문은 기록하지 않으며 Audit 저장 실패 시 업무 Transaction도 실패한다.

## 검증

- Core·Compile·결정적 증거: `npm run verify:api-authorization -- --no-write`
- OpenAPI 계약: `npm run verify:openapi-contract -- --no-write`
- 권한 Core 증거: `docs/03_evidence/release_1/R1-M4-04/authorization-core-summary.json`

# Studio Workspace 기본 정책 Canon 설계

## 1. 상태와 목표

운영 Workspace `workspace-be846e417dc13c1ec9f866ff`는 Source 조회와 Egress Policy는 정상이나 `workspace_policies`, `ruleset_bindings`, `weight_profiles`가 없어서 Product Studio 정책 Projection을 만들 수 없다. `knowledge_scopes`는 기존 Question 실행으로 뒤늦게 생성됐지만 Workspace 생성 시점의 일관된 기본 정책 세트는 아니다.

목표는 기존 Workspace와 이후 생성되는 Workspace가 Studio 조회 전에 동일한 보수적 기본 정책 Canon을 갖도록 보장하는 것이다. 인증·Source·Question·Egress Policy의 현재 외부 동작은 유지한다.

## 2. 선택한 방식

Migration `0013`이 다음 두 경계를 함께 제공한다.

1. 기존 `workspaces` 전체에 누락된 기본 Canon만 idempotent backfill한다.
2. `workspaces`의 `AFTER INSERT` Trigger가 신규 Workspace의 기본 Canon을 같은 PostgreSQL transaction에서 생성한다.

Studio GET에서 lazy write하거나 Browser가 기본값을 조립하지 않는다. 인증 SQLite와 PostgreSQL 사이의 새 분산 transaction도 만들지 않는다.

## 3. 기본 정책 계약

기본 세트는 외부 전송이나 결과 승인을 자동 허용하지 않는다.

| Canon | 기본값 | 의미 |
| --- | --- | --- |
| `WorkspacePolicy` | `data_area=cloud_sync`, `authority_policy=workspace_admin`, `active/current=true`, `version=1` | Workspace 관리 권한을 기본 정책 책임자로 두며 실제 권한 판정은 기존 Authorization Service가 계속 수행 |
| `KnowledgeScope` | `scope=workspace`, `active/current=true`, `version=1` | 현재 Workspace의 Source만 기본 범위로 사용 |
| `WeightProfile` | `profile=trusted-source-v2`, `active/current=true`, `version=1` | 검증된 Source 우선 가중치 |
| `RuleSetReference` | `name=default-review-required`, `active/current=true`, `version=1` | 기본 RuleSet 식별자 |
| `RuleSetVersionSnapshot` | `rules=[]`, `review_condition=review_required`, `active/current=true`, `version=1` | 자동 적합 판정을 만들지 않고 사람 검토를 요구 |
| `RuleSetBinding` | 기본 Reference/Snapshot 결속, `active/current=true`, `version=1` | Studio 생성 시 immutable RuleSet Version 고정 |

Egress 기본값은 Migration `0012`의 Organization/Workspace `deny_external` Binding을 그대로 사용한다. `0013`은 Egress Policy를 완화하거나 재작성하지 않는다.

모든 ID는 `tenant_id + workspace_id + entity kind`로 결정론적으로 계산한다. `created_by`와 `trace_id`는 `migration:0013` 계열로 식별하며 사용자 행위로 가장하지 않는다.

## 4. 기존 데이터 보존과 backfill

- 해당 Canon의 유효한 최신 행이 이미 있으면 그대로 사용하고 새 기본 행을 만들지 않는다.
- `KnowledgeScope`가 이미 있고 `WeightProfile`만 없으면 최신 유효 Scope를 참조해 WeightProfile만 생성한다.
- RuleSet Reference·Snapshot·Binding 중 일부만 있으면 기존 결속을 임의 조합하지 않는다. `0013` 전용 결정론 ID 세트를 완성하되 다른 RuleSet 행은 변경하지 않는다.
- 기존 Run, RunSnapshot, EgressDecision, RoutingDecision, Source, Output에는 소급 변경하지 않는다.
- immutable Canon의 UPDATE/DELETE는 사용하지 않는다.
- Canon JSON, canonical text와 SHA-256 digest는 기존 DB 검증 Trigger를 통과해야 한다.

## 5. Runtime 동작

`PostgresStudioWorkspaceRepository._policy_projection()`의 fail-close 계약은 유지한다. 필수 정책이 없거나 inactive/stale/scope mismatch이면 Studio 목록과 생성은 계속 안전 오류로 중단한다.

정상 기본 세트가 있으면 Studio 목록은 빈 `outputs`와 6개 잠금을 반환한다. Source 목록과 Question은 Studio 조회 실패와 독립적으로 유지된다.

`STUDIO_DATABASE_UNAVAILABLE`은 실제 pool/SQL 장애에만 사용한다. 정책 누락은 `POLICY_PROJECTION_UNAVAILABLE`로 유지하며 Runtime 공개 Safe Error allowlist에 포함해 DB 장애로 오인되지 않게 한다.

## 6. Migration과 rollback

Upgrade 순서:

1. 기본 Canon 생성 함수 정의
2. 기존 Workspace backfill
3. 필수 행·FK·digest 검증
4. 신규 Workspace `AFTER INSERT` Trigger 설치

Downgrade 순서:

1. Trigger 제거
2. 함수 제거
3. `created_by='migration:0013'`이고 결정론 ID가 일치하는 `0013` 생성 행만 FK 역순으로 삭제
4. 사용자 또는 다른 Migration이 만든 행은 보존

Downgrade 후 `0012` schema와 Egress deny Binding은 그대로 남아야 한다. Upgrade→downgrade→upgrade가 결정론적으로 재실행돼야 한다.

## 7. 보안과 운영 경계

- SQL은 정적 Migration SQL과 parameterized repository SQL만 사용한다.
- RLS/FORCE RLS와 `daon_app` 권한을 우회하지 않는다.
- Trigger는 해당 `NEW.tenant_id/workspace_id`만 생성하며 다른 Tenant·Workspace를 조회하거나 수정하지 않는다.
- 외부 전송은 계속 Organization/Workspace deny precedence와 Step-up 계약을 따른다.
- 브라우저 코드는 same-origin `/bff/api/...`만 사용한다.
- 오류 응답에는 SQLSTATE, 내부 주소, stack, DSN 또는 자격정보를 포함하지 않는다.

## 8. 검증 조건

자동 검증:

- Migration 정적 계약과 실제 PostgreSQL 15/18 upgrade·downgrade·reapply
- 기존 Workspace의 누락 전체·부분 누락·완전 구성 idempotency
- 신규 Workspace INSERT 한 transaction에서 6개 기본 Canon 생성
- Canon digest, FK, immutable, RLS, cross-tenant 0건
- Studio 목록이 `outputs=[]`, 6 locks, 공개 정책 오류 exact code를 반환
- Source/Question/Auth/Egress 전체 관련 회귀, OpenAPI/BFF, build·TypeScript·boundary

운영 검증:

- 사전 backup과 rollback image/tag 보존
- ysna-server Migration `0012→0013`
- 로그인 후 Source 5건 유지
- Studio의 `STUDIO_DATABASE_UNAVAILABLE`/정책 누락 경고 해소
- 빈 저장 산출물과 6개 잠금 표시
- API·worker·공용 DB·proxy·object storage 경계 불변

## 9. 제외 범위

- 사용자가 기본 RuleSet 내용을 편집하는 새 UI
- Organization/Workspace Egress 정책 완화
- 기존 Source 실패·검토 상태의 자동 재처리
- Studio 산출물 자동 생성·승인·전달·지식 등록
- 인증 또는 Workspace 역할 모델 변경

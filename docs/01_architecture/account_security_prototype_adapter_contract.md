# 계정·조직·보안 Prototype·Adapter 승계 계약

## 1. 판정과 경계

`R1-M2-06`은 M3~M8이 승계하는 Production-bound 계정·조직·보안 UI 기준선이다. 현재 구현은 Browser React View와 순수 Domain Reducer의 `prototype_fixture`이며 실제 Auth·API·DB·Session·Key·MFA·Egress를 실행하거나 성공으로 주장하지 않는다.

| 구분 | 이번 범위 | 후속 실제 책임 |
| --- | --- | --- |
| 실제 외부 효과 | API·DB·OIDC·Session/Sync Key·MFA·전송·재색인 `0건` | M3 Client, M4 Auth/API/권한, M5 Data/Sync, M6 Index, M8 Studio |
| `403` | `HTTP 403 계약 Preview · 실제 API 미실행` | M4-04 실제 403/404 비노출과 RLS·Service Authorization |
| Step-up | Actor·Action·Target·Policy Version 결합 순수 전이 | M4-03 실제 OIDC/MFA와 서버 강제 |
| 장치 철회 | 상태·Audit Preview | M3/M4/M5 실제 Device·Session·Secure Store·Sync Key |
| 영역 이동 | 건너뛸 수 없는 5단계 Preview | M5/M6 실제 Copy·Version·Index·Egress |

## 2. 재사용 파일과 소유권

| 영역 | 파일 | 승계 계약 |
| --- | --- | --- |
| Domain Model | `packages/ui/src/account-security-model.js` | 역할 7·권한 8, Persona 분리, 조직 정책, StepUpAuthorization, AccessDecision, 영역 이동, Audit의 순수 판정기를 재사용한다. |
| React View | `packages/ui/src/account-security-pane.jsx` | Account·Organization UI Projection과 상태 보존·접근성 상호작용을 재사용한다. |
| Route | `apps/web/app/settings/account/page.jsx`, `apps/web/app/settings/organization/page.jsx` | M2-01 `account_settings`·`organization_settings` Route/Screen 정본을 직접 소비한다. |
| 공용 Style | `packages/ui/src/workspace.css` | 1920×1080·본문 12px·네 폭 반응형과 Tooltip 인터페이스를 유지한다. |
| Studio Fixture | `packages/ui/src/studio-workflow-model.js` | 불변 `output-version-001`·ApprovalRequest 계보를 과거 결과 재검증 Fixture로 참조한다. 실제 저장 정본으로 간주하지 않는다. |

## 3. 역할·권한 Adapter 계약

- `NavigationPersona`는 Route 노출 축이고 `MembershipRole`은 실제 작업 권한 축이다. Persona에서 MembershipRole·Write를 추론하지 않는다.
- `personal_owner | organization_admin | workspace_admin | editor | reviewer | approver | viewer`와 세부 권한 8종은 독립 정본이다.
- Control·Fixture Action은 같은 순수 Authorization 판정기를 사용한다. UI 숨김은 서버 권한 강제가 아니다.
- 정책 Preview를 포함한 권한 판정은 현재 활성 Membership의 Role·Grant만 정본으로 사용한다. Caller가 전달한 `role`·`persona`·`grants`는 권한을 상승시키지 못하며 현재 Membership Role과 다르면 거부한다.
- 강제 RuleSet Binding은 조직 관리자만, 선택형 Binding은 Workspace 관리자·조직 관리자만 변경한다.
- M4 Adapter는 Tenant·Workspace·Membership·Capability를 현재 요청마다 재검증하고 `AUTHORIZATION_DENIED | CURRENT_ACCESS_DENIED`를 동일 의미로 반환한다.
- `SENSITIVE_ACTION_REGISTRY`는 민감 Action별 Required Permission, 허용 MembershipRole과 Target 종류를 고정한다. 최소 7종 또는 조직이 명시 추가한 완전한 Registry Entry 외 문자열은 발급 전 `STEP_UP_ACTION_NOT_ALLOWED`로 거부한다.
- Step-up은 권한을 부여하지 않는다. 발급 전과 소비 직전에 Actor·현재 Membership·Capability·Tenant·Workspace·Policy Version을 재검증하며, 발급 뒤 회수·변경 시 Authorization을 소비하지 않고 `CURRENT_ACCESS_DENIED`로 종료한다.

## 4. 정책·Provider 안전 경계

- UI는 요청값·유효값·잠금 이유·Policy Version을 함께 표시한다.
- Provider는 불투명 Profile/Deployment ID, 허용 상태, 데이터 영역과 정책만 표시한다.
- Secret·Credential·Raw Provider 오류·내부 Host/Port·Daon 내부 식별자는 Fixture·DOM·Console·증거에 넣지 않는다.
- Browser Adapter는 same-origin BFF·Route Handler 뒤에 둔다. 절대주소, `localhost`, `127.0.0.1`, Docker 내부 주소와 `NEXT_PUBLIC_API_BASE_URL` Client Fetch를 금지한다.

## 5. Step-up·장치·Audit 교체 계약

| 유지할 순수 계약 | 실제 Adapter 교체 |
| --- | --- |
| 민감 작업 최소 7종과 조직 추가만 허용 | M4 정책 저장소·서버 Allowlist |
| 불투명 ID, Actor·Action·Target·Policy Version, issued/used/expired/failed | M4 단기 Authorization 발급·검증·원자적 1회 소비 |
| 미충족 `STEP_UP_REQUIRED`, 오대상·만료·재사용 거부와 변경 0건 | M4 API에서 Domain Write 이전 강제 |
| 장치 `registered/trusted/attention_required/revoked` Metadata | M3/M4/M5 실제 Device 등록·Session·Sync Key·Secure Store |
| Append-only Audit Event 배열 | M4-02 불변 Audit 저장소·위변조 방지 |

종료 Step-up과 철회 장치 상태는 되살리지 않는다. 재인증·재등록은 새 ID의 별도 수명주기로 만든다.

영역 이동은 `authorization_sensitive_check`와 `explicit_approval`에서 현재 `data_realm_move` 권한을 검사하고, `transfer_preview`·`version_audit` 진입 전에는 승인에 사용한 동일 Step-up Scope와 현재 권한을 다시 검사한다. 실패하면 단계·Approval Preview·실제 Count를 변경하지 않는다.

## 6. 과거 결과 현재 권한 재검증

- 과거 OutputVersion·RunSnapshot·EvidenceReference는 불변이다.
- Read·Citation·원문 열기·Export·Delivery·KnowledgeRegistration·Rerun마다 현재 Membership·ACL·SourceVersion 권한·Policy Version으로 새 `AccessDecision`을 만든다.
- `available | partially_redacted | access_blocked`와 Masking Reference·사유를 분리한다. 안전 분리가 불가능하면 전체 차단한다.
- Rerun은 과거 Run을 변경하지 않고 현재 ACL·영역·정책·비용 한도의 새 Run Preview와 `previousRunId`를 만든다.
- 이미 외부 Export된 사본의 회수 성공은 주장하지 않고 운영 경고·Audit로 남긴다.

M4/M5/M8 실제 Adapter도 과거 Snapshot 권한을 현재 권한으로 사용해서는 안 된다.

## 7. Client·상태 보존 계약

- Web 폭 1920·1200·800·500은 모두 `client_type=web`이며 Organization 기능을 반응형으로 유지한다.
- Android·iOS 미지원은 폭이 아니라 명시 `client_type=android | ios`에서 `unavailable`과 `Web·Windows에서 이어서 작업`으로 판정한다.
- Account↔Organization History 이동과 폭 변경 뒤 선택 역할·정책·OutputVersion·Step-up 상태를 보존한다.
- `projectAccountSecurityRoute(state.screen)`이 현재 URL과 `account_settings/account_settings` 또는 `organization_settings/organization_settings`, 제목을 함께 투영한다. 시작 Route의 Props를 내부 전환 뒤 재사용하지 않는다.
- M3에서 Native Adapter를 연결해도 지원 여부를 CSS Breakpoint로 판정하지 않는다.

## 8. 금지된 임시 경로

- 실제 Backend 성공으로 보이는 임시 Mock Server·Browser 직접 API 주소
- Persona만으로 MembershipRole·Write 권한 부여
- Step-up을 ApprovalRequest나 장기 Session으로 대체
- 철회된 Device·사용된 Step-up 재활성화
- 과거 AccessDecision 재사용 또는 원본 OutputVersion 마스킹 수정
- 승인 전 Egress·전송·대상 SourceVersion·재색인 성공 표시
- Secret·Token·Session·Sync Key 실값 생성·저장·표시

실제 Adapter 도입이 공개 API·데이터·보안 경계를 바꾸면 구현하지 않고 어울1의 설계 판단과 신산님의 승인 절차를 따른다.

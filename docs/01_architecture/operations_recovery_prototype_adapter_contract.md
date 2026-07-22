# 운영·알림·복구 Prototype Adapter 승계 계약

## 판정

`R1-M2-07`은 M3 이후가 재사용할 Operations·Notifications·Recovery의 Production-bound 상태·표현 기준선이다. 현재 구현은 결정론적 Fixture와 순수 Reducer만 실행하며 실제 API·DB·Queue·Worker·Model·Connector·Backup·Restore·Update·Rollback·Notification Write를 수행하지 않는다. 화면은 `prototype_fixture`, `deferred_actual`, `실제 API 미실행`을 직접 표시한다.

## 재사용·교체 소유권

| 경계 | 현재 재사용 정본 | 실제 Adapter 교체 책임 |
| --- | --- | --- |
| Service·Queue·Model·Node·Connector·비용·Backup·Update Projection | `packages/ui/src/operations-recovery-model.js` | M9 Operations Status·Backup·Release Adapter |
| Alert 안정 Key·Count·세대와 Incident 순차 상태 | 같은 순수 Model·Reducer | M4 Notification BFF, M5 Incident·Audit 저장 정본 |
| `waiting_model` Readiness·수동 재처리·중복·Backoff·계보 | 같은 순수 Model·Reducer | M6-14 Source Processing Adapter·Worker |
| Operations·Notifications·Tooltip·ARIA·반응형 Pane | `packages/ui/src/operations-recovery-pane.jsx` | M3 Client Shell이 Component·ViewState 계약을 유지 |
| Route | `apps/web/app/operations/page.jsx`, `apps/web/app/notifications/page.jsx` | M4 same-origin BFF 연결 |
| Source·Run·권한 Snapshot | M2-03·M2-04·M2-06 공개 계약을 참조 | M5 저장 정본과 M6 실행 Adapter |

## 불변 Domain 계약

- 자동 Queue는 `auto` 또는 정책이 허용한 `local_only`에서 필수 역할의 Deployment·Node·Provider가 `ready/healthy`인 Readiness Event에만 한 번 생성한다.
- `pinned`·직접 선택은 자동 실행하지 않는다. 수동 요청은 현재 활성 Membership·Capability·Tenant·Workspace·Source ACL을 다시 확인하며 Payload Role·Grant를 권한 정본으로 신뢰하지 않는다.
- 이전 ProcessingRun은 불변이다. 새 Preview Run은 새 ID, `retry_of_processing_run_id`, Trigger와 현재 ACL·영역·RoutingPolicyVersion·비용·Egress Snapshot을 가진다.
- SourceVersion·필수 역할별 활성 Run, Event ID, Idempotency Key와 Backoff는 새 Run 0건으로 억제하고 안정 Code·Append-only Audit를 남긴다.
- 결과는 `ready/READY_GATE_PASSED`, `needs_review/NO_POLICY_CANDIDATE`, `waiting_model/NO_AVAILABLE_UNDERSTANDING_MODEL`을 구분한다. Parser/OCR-only 또는 ASR-only 결과로 Ready를 만들지 않는다.
- Alert Key는 Tenant·Workspace·Resource·Safe Code·Policy Version의 안정 조합이다. 활성 Incident 반복 신호는 Count만 갱신하고, `recovered` 뒤 같은 Key는 새 세대·Incident로 분리한다.
- Incident는 `detected→warning→restricted→recovering→recovered` 순서를 건너뛰지 않으며 실패 Run·Alert·Audit를 삭제하지 않는다.

## 축소 운영·보안 경계

| 장애 | 유지해야 할 판정 |
| --- | --- |
| Daon | `DAON_ONLY_DEGRADED` |
| External LLM | `FROZEN_POLICY_CANDIDATES_ONLY` |
| Local LLM | `EXTERNAL_AUTO_SWITCH_FORBIDDEN` |
| 인터넷 | `INTERNET_DEPENDENT_ONLY` |
| Index | `READY_SOURCES_ONLY` |
| Evidence Store | `APPROVAL_DELIVERY_BLOCKED` |

- 장애 Fixture는 다른 Service를 바꾸지 않으며 무단 Fallback과 실제 외부 효과는 항상 0건이다.
- 비용 한도는 `policy_blocked/COST_LIMIT_EXCEEDED`이며 같은 Frozen Context 자동 재시도를 허용하지 않는다.
- 유효 Step-up 없는 Recovery Write는 `STEP_UP_REQUIRED`, 승인 없는 Restore·Rollback은 `G9_DRILL_APPROVAL_REQUIRED`, 승인 없는 Update는 `G9_DEPLOY_APPROVAL_REQUIRED`다.
- 과거 OutputVersion은 변경하지 않고 `partially_redacted`, `access_blocked` AccessDecision 집계만 표시한다.

## C01 Fail-close 보정

- `complete-retry-preview`는 `actualRunCreated=false`인 현재 Prototype 진행 Run만 처리한다. `immutable:true`, 최초·과거·종료 Run과 다른 SourceVersion·역할 Run은 안정 Code로 거부하고 Source·Run·Queue·성공 Audit를 바꾸지 않는다.
- 새 Run의 부모는 같은 SourceVersion·필수 역할에서 가장 최근의 재시도 가능한 실패 Run이다. 부모 전체 객체는 생성·완료 전후 깊은 불변이다.
- 선택 Mode는 `auto | local_only | pinned | direct`, Outcome은 `ready | policy_blocked | runtime_exhausted`만 허용한다. 자동 Event는 Source가 정확히 `waiting_model`일 때만 평가한다.
- Mode·Source 상태·Event ID·Outcome을 업무 상태 적용 전에 검증한다. `INVALID_SELECTION_MODE`, `SOURCE_NOT_WAITING_MODEL`, `DUPLICATE_TRIGGER_EVENT`, `INVALID_RETRY_OUTCOME`은 Event·Run·Queue 변경 0건으로 닫힌다.
- Recovery Preview는 현재 활성 Membership·Tenant·Workspace와 Action별 Capability를 다시 검사한다. Payload의 Role·Grant·Step-up 상태·임의 승인 문자열은 권한 정본이 아니다.
- `StepUpAuthorization`은 ID로 조회하고 Actor·Action·Target·PolicyVersion·`issued`·미만료·미사용을 모두 검사한다. G9는 `drill | deploy`, Target·Tenant·Workspace·PolicyVersion·`approved`·승인자·승인 시각이 정확히 일치해야 한다.
- 유효 Step-up과 G9도 `RECOVERY_PREVIEW_ONLY`만 만들며 실제 효과는 0건이다. 성공 시 Step-up을 한 번 사용 처리하고 Audit에 Step-up·G9 정본 ID를 남긴다.

## C02 역할·재시도 부모 Fail-close 보정

- Recovery Preview 실행 역할은 현재 MembershipRole 정본의 `organization_admin`만 허용한다. `operator`는 NavigationPersona이며 MembershipRole이나 쓰기 권한으로 해석하지 않는다. 검증 순서는 `활성 Membership → Tenant/Workspace → 정본 MembershipRole → Recovery 역할 Allowlist → Action Capability → StepUpAuthorization → G9 Approval`이다.
- `viewer`는 Recovery Capability와 유효한 Step-up·G9를 모두 보유해도 `RECOVERY_AUTHORIZATION_DENIED`다. 이 거부는 Preview, 성공 Audit, Step-up 소비와 실제 효과를 만들지 않는다.
- 자동 Readiness와 수동 Retry는 같은 SourceVersion·필수 역할의 가장 최근 실패 ProcessingRun을 새 Run의 부모로 먼저 찾아야 한다. 부모가 없으면 예외 없이 `RETRY_PARENT_NOT_FOUND`로 닫힌다.
- `RETRY_PARENT_NOT_FOUND`는 Readiness Event, processed Event, Idempotency Key, Run, Queue, 성공 Audit와 Source 상태를 변경하지 않는다. 보안 거부 Audit은 후속 실제 Adapter가 정책상 선택할 수 있지만 성공으로 기록해서는 안 된다.

## API·Queue·Backup·Update 경계

- 예정 수동 처리 계약은 `POST /api/v1/sources/{id}/processing-runs`이지만 M2에서는 Domain Action·Preview만 만들고 요청하지 않는다.
- M4 BFF는 Browser에서 same-origin 상대 경로만 사용한다. API 절대주소, Loopback 주소, Docker Host/Port와 `NEXT_PUBLIC_API_BASE_URL` Client Fetch는 금지한다.
- 실제 Queue·Worker·DB·Object Storage·Model·Connector·Notification Adapter는 M4~M6 소유다. Fixture를 임시 Backend나 성공 응답으로 승격하지 않는다.
- 실제 Backup·Restore·Update·Rollback은 M9 소유이며 G9-DRILL/G9-DEPLOY 승인 전 실행하지 않는다. M2 버튼은 Preview와 `deferred_actual`만 표현한다.
- R1-D022 Next Canary는 개발·GitHub Check·ysna-server 격리 검증 전용이다. 운영 Release로 승격하거나 Dependency·Lockfile·Toolchain을 이 계약에서 변경하지 않는다.

## 후속 검증 책임

- M3: ViewState·Component·ARIA·네 폭 상태 보존 승계.
- M4: same-origin BFF와 Notification Deep Link·읽음 저장 계약 연결.
- M5: Incident·Alert·Audit·Run Snapshot의 불변 저장 및 Trace 계보.
- M6-14: 실제 Readiness Event·Queue·Worker·Retry 결과 연결.
- M9: 실제 Operations Status, Backup/Restore Drill, Update/Rollback Adapter와 G9 승인 경계.

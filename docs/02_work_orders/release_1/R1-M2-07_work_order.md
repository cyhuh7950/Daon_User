# R1-M2-07 작업지시서 — 운영·알림·복구

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M2-07` |
| issue_id | `R1-M2-07-I001` |
| 작업 | 운영 상태·알림·축소 운영·`waiting_model` 자동/수동 재처리·복구 Production-bound Prototype |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| 기준 Branch | `codex/r1-m2-07` |
| 기준 SHA | `3dcad0563a98274cbcd55dfa3250afff0609517f` |
| 선행 작업 | `R1-M2-01`~`R1-M2-06` 완료·Merge |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M2-07_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-07_attempt-1.md` |

어울2는 착수 전에 아래 정본을 EOF까지 읽고 SHA-256을 대조한다. 요약본으로 대체하지 않는다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| `docs/04_test_reports/release_1_test_plan.md` | `359404A190D248E94F2BE4A69CB285D10422FA426C32D4C5409F868F4CA4768B` |
| `docs/04_test_reports/release_1/scenarios/06_operations_recovery.md` | `431803DAB5C1EE6E04D38CE7E509F92B179E080676E67043750B6A47475C86B7` |
| `docs/01_architecture/source_authority_prototype_adapter_contract.md` | `6B0E49855920FA3AE82A3327532CE5E88C1B1CE05680AE440C91B316666F6F0D` |
| `docs/01_architecture/run_model_evidence_prototype_adapter_contract.md` | `21C4F4339D15F06084B10281C40D4BEA1B1B894325C90074EF25007E94CAB6E8` |
| `docs/01_architecture/account_security_prototype_adapter_contract.md` | `667199827C7B4DEE7C44ECBFCE52A92AA191E60FD899271D2E67639C5E65A63D` |
| `docs/01_architecture/product_sitemap.md` | `102F1D1C5E47398E426EB0028200E8ED74F010428990EABA9B527C2AEA554925` |

## 2. 목적과 사용자 완료 여정

조직 관리자와 운영자는 `/operations`에서 서비스·Queue·모델·Node·Connector·비용·Backup·Update 상태와 보안 경고를 한 화면에서 확인하고, `/notifications`에서 중복 억제된 경고·복구 알림을 추적한다. `waiting_model` Source는 설계된 조건에서만 자동 또는 수동으로 새 ProcessingRun을 만들며, Daon·LLM·인터넷·Index·Evidence 장애는 허용 범위만 축소하고 복구 과정과 Audit를 화면에서 끝까지 보여준다.

최소 완료 여정은 다음과 같다.

1. `/operations`에서 API·Worker·DB·Object Storage, 처리/실패 Queue, Model·Runtime Node, Daon·인터넷 Connector, 저장·Token·비용, Backup·Restore·Update·Rollback 상태를 확인한다.
2. `waiting_model` Source에서 필요한 역할·이전 ProcessingRun·Routing Mode·Backoff·중복 억제·현재 정책을 확인한다.
3. `auto` 또는 자동 재처리가 허용된 `local_only`에서 ModelDeployment·RuntimeNode·Provider의 `ready/healthy` Readiness Event를 발생시켜 새 ProcessingRun 한 건만 자동 Queue한다.
4. `pinned`·직접 선택은 Readiness Event만으로 자동 실행하지 않고, 권한 있는 사용자의 수동 재처리에서만 새 ProcessingRun을 만든다.
5. 같은 SourceVersion·필수 역할에 대한 중복 Event·Idempotency Key·활성 Run 충돌은 새 Run을 추가하지 않고 억제 사유를 표시한다.
6. 새 Run은 실패한 이전 Run을 수정하지 않고 `retry_of_processing_run_id`, `trigger_type`, `trigger_event_id`, 현재 ACL·영역·RoutingPolicyVersion·비용·외부 전송 Snapshot과 Audit를 남긴다.
7. Daon·External LLM·Local LLM·인터넷·Index·Evidence Store 장애를 각각 주입해 설계된 축소 동작, 금지된 우회 0건과 사용자 경고를 확인한다.
8. 경고→제한→자동/수동 새 Run→복구를 수행하고 운영 화면·알림·Audit의 상태가 일치하는지 확인한다.
9. Step-up 실패·만료, 과거 결과 `partially_redacted`·`access_blocked`, 정책 차단과 비용 경고를 운영 화면과 알림에서 추적한다.
10. Backup·Restore·Update·Rollback은 상태·복구 목표·훈련 결과·요청 Preview만 표시하고 실제 파괴적 Restore·운영 배포 성공으로 위장하지 않는다.

## 3. 구현 범위

### 3.1 Route·화면·상태 정본

- `packages/contracts/navigation.json`과 `screens.json`의 `operations`, `notifications` 정본을 수정 재정의하지 않고 실제 Next Prototype Route `/operations`, `/notifications`에서 소비한다.
- Operations는 Web·Windows 책임이다. 1920·1200·800·500px Browser는 모두 `client_type=web`이므로 폭이 좁아도 기능을 숨기지 않는다. Android·iOS 제한은 폭 추론이 아닌 명시 `client_type` Projection으로 `unavailable`을 표시한다.
- `OperationsRecoveryViewState` 또는 동등한 단일 정본에 선택 Incident·Service·SourceVersion·ProcessingRun·Readiness Event·Queue·Alert·Recovery Preview·Audit를 보존한다.
- `/operations`↔`/notifications` 이동과 네 폭 전환 뒤 선택 Incident, Queue Filter, Alert 읽음 상태, Recovery 단계가 초기화되지 않는다.
- 화면 상태 `loading`, `empty`, `ready`, `warning`, `error`, `forbidden`, `unavailable`을 명시적으로 투영한다.

### 3.2 운영 상태와 경고

- Service 상태는 API·Worker·DB·Object Storage를, 처리 상태는 Source Processing·Index Build·실패 Queue를 표시한다.
- Model 상태는 Local·Internal·External Deployment, 역할, Health·Capacity·Runtime Node를 표시한다. Secret·Credential·내부 Host/Port·Raw Provider 오류는 표시하지 않는다.
- Connector 상태는 Daon·인터넷을, 비용 상태는 사용자·조직별 저장·Token·비용 한도와 `COST_LIMIT_EXCEEDED`를 표시한다.
- Backup·Restore·Update·Rollback은 현재 상태, 마지막 성공/검증 시각, 복구 목표, 훈련 결과와 요청 가능 여부를 표시한다.
- Alert는 `alert_key = tenant + workspace + resource + safe_code + policy_version` 또는 동등한 안정 키로 중복을 억제한다. 같은 활성 Incident의 반복 신호는 Count·최근 시각만 갱신하고 중복 알림을 추가하지 않는다.
- Severity·상태·영향 범위·재시도 가능 여부·사용자 조치·안전 Code·Trace ID를 표시하며 Stack Trace·DB/Host·API Key 이름·Provider 원문을 노출하지 않는다.
- 운영 화면에는 `waiting_model` 수·필요 역할·자동/수동 Queue·Backoff·중복 억제와 Step-up 실패/만료·과거 AccessDecision 차단/마스킹 현황을 포함한다.

### 3.3 `waiting_model` 자동·수동 재처리

- M2-03 Source 계약의 `waiting_model`과 Modality별 Ready Gate를 재사용한다. Parser/OCR-only 또는 ASR-only 결과로 `ready`를 만들지 않는다.
- 자동 Queue는 선택 Mode가 `auto`, 또는 정책상 자동 재처리가 허용된 `local_only`이고 필요한 ModelDeployment·RuntimeNode·Provider가 `ready/healthy`로 복귀한 Readiness Event에서만 한 번 생성한다.
- `pinned`와 직접 선택은 자동 Queue를 금지하고 사용자에게 수동 재처리 또는 대안 선택을 제안한다.
- 수동 재처리는 현재 활성 Membership·Tenant·Workspace·Source ACL과 명시 Capability를 모두 통과한 Source 소유자·Workspace 관리자·운영자만 요청할 수 있다. Navigation Persona나 Action Payload의 Role/Grant를 권한 정본으로 신뢰하지 않는다.
- 수동 API 계약은 `POST /api/v1/sources/{id}/processing-runs`이지만 M2에서는 실제 요청하지 않는다. Domain Action과 화면 Preview만 제공하고 실제 Adapter Owner를 명시한다.
- 이전 ProcessingRun은 불변이다. 새 Run은 새 ID, `retry_of_processing_run_id`, `trigger_type=readiness_event|manual`, `trigger_event_id`, 현재 Policy/ACL/DataEnvelope/비용/Egress Snapshot을 가진다.
- SourceVersion·필수 역할별 활성 ProcessingRun은 하나만 허용한다. Event ID와 Idempotency Key 중복, Backoff 미경과, 활성 Run 존재는 안정 Code와 변경 0건으로 거부한다.
- 성공 Fixture는 Modality별 이해·검증 뒤 `indexing→ready`, 정책 후보 0은 `policy_blocked`와 Source `needs_review`, Runtime 재소진은 새 Run `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`과 Source `waiting_model` 유지로 투영한다.
- 자동·수동 Trigger, 이전 Run, Readiness Event, 새 Snapshot, 억제·실패·성공 결과를 Append-only Audit Preview에 기록한다.

### 3.4 장애별 축소 운영

| 장애 | 필수 Prototype 판정 |
| --- | --- |
| Daon Connector | Daon 지식·엔진만 비활성. 유효 검증 RuleSet Snapshot은 계속 적용; 유효 Snapshot 없는 강제 Binding 대상 Run만 차단; Binding 없는 Workspace 독립 기능 유지 |
| External LLM | `auto`만 같은 Frozen RoutingPolicyVersion·역할·영역·Egress 안의 Local·Internal·허용 External 후보를 자동 시도; `pinned`/직접 선택은 무단 변경 없이 제안만 표시 |
| Local LLM | 명시 전송 승인 없이 External 자동 전환 0건 |
| 인터넷 Connector | 보유 지식 실행 가능 범위를 안내하고 인터넷 의존 기능만 축소 |
| Index | Ready Source만 사용하고 검색·생성의 누락 범위를 표시 |
| Evidence Store | 근거 무결성을 보장할 수 없으므로 승인·전달을 차단 |

- 장애 Fixture는 하나씩 독립 주입하고 다른 서비스 상태를 임의로 바꾸지 않는다.
- Incident는 `detected→warning→restricted→recovering→recovered` 또는 동등한 명시 상태를 가지며 단계를 건너뛰지 않는다.
- 복구는 기존 실패 Run/Alert/Audit를 삭제하지 않고 새 Recovery Attempt와 해결 시각을 연결한다.
- 비용 한도는 `policy_blocked/COST_LIMIT_EXCEEDED`이며 같은 Frozen Context에서 자동 재시도하지 않는다.

### 3.5 권한·정책·보안 운영 신호

- `/operations` Route 노출 Persona와 실제 운영 Action 권한을 분리한다. `operator` Persona만으로 조직 콘텐츠·정책·복구 Write를 허용하지 않고 현재 Membership·Capability를 재검증한다.
- 조직 관리자와 운영자도 다른 Tenant·Workspace, 권한 회수, Policy Version 불일치, Step-up 필요 Action에서 거부되어야 한다.
- 실제 정책 변경·Credential 변경·영구 삭제/Restore·장치/Key 철회는 M2-06의 Step-up 계약을 재사용한다. 유효 Step-up 없이 Recovery Write를 시작하지 않는다.
- 과거 결과 운영 신호는 원본 OutputVersion을 변경하지 않고 `AccessDecision` 집계만 표시한다.
- 운영 Fixture·DOM·Console·Screenshot·보고서에 개인정보 원문, Token, Password, Cookie, API Key, Credential, 내부 주소를 넣지 않는다.

### 3.6 Backup·Restore·Update·Rollback 경계

- Backup Health, 마지막 검증, RPO/RTO 정책값, Restore Drill 결과, Update Channel·현재/후보 Version, Rollback 가능 여부를 안전 Metadata로 표시한다.
- 실제 Backup 생성·Restore·삭제·배포·Update·Rollback·DB·파일 조작은 수행하지 않는다. 버튼은 `prototype_fixture` 요청 Preview와 `deferred_actual`을 분리한다.
- 운영 데이터 Restore와 외부 배포는 G9-DRILL/G9-DEPLOY 승인 없이는 실행 불가임을 화면과 Adapter 계약에 명시한다.
- 이번 Browser 검증에서 성공으로 인정하는 것은 상태 전이·권한·중복 억제·Audit Preview뿐이다.

### 3.7 알림과 상태 보존

- `/notifications`는 경고·진행·권한·복구 알림을 시간·Severity·상태·대상·안전 Code·Trace ID와 함께 표시하고 Operations Incident로 이동할 수 있어야 한다.
- 알림 읽음 처리는 ViewState의 Prototype 전이이며 실제 서버 Notification Write 성공으로 표시하지 않는다.
- 중복 Alert는 한 항목의 Count로 표시하고, `recovered` 후 같은 Key의 새 Incident는 새 세대/Incident ID로 분리한다.
- 자동 재처리 Queue, 수동 요청, 억제, 실패, 복구 완료가 각각 알림과 Audit에 일관되게 나타난다.

### 3.8 Prototype 정직성·접근성·same-origin

- 실제 API·Worker·DB·Object Storage·Queue·Model·Connector·Backup·Update·Notification Adapter가 없음을 명시하고 성공으로 위장하지 않는다.
- Browser 실행 코드는 same-origin 상대 경계만 유지한다. API 절대주소, `localhost`, `127.0.0.1`, Docker Host/Port, `NEXT_PUBLIC_API_BASE_URL` 직접 호출, Server 내부 주소 노출을 금지한다.
- 사용자 입력 Fixture는 허용 Enum·ID·길이를 검증하고 HTML을 직접 삽입하지 않는다.
- 기준 화면 1920×1080, 본문·Form 12px, 작은 설명 10px, 보조 9px, 사이드바 14px, 제목 16px를 유지한다. 상시 설명 박스 대신 `i` Tooltip/Popover를 사용한다.
- Keyboard·Focus·ARIA Live 상태, 비색상 Severity, 500px 문서 가로 Overflow 0을 확인한다.

## 4. Production-bound 재사용·교체 계약

- 순수 Operations/Recovery Domain Model·Reducer, 상태·안전 Code·중복 억제·재처리·Incident Projection을 실제 Adapter가 재사용할 수 있게 UI와 분리한다.
- `docs/01_architecture/operations_recovery_prototype_adapter_contract.md`에 재사용 파일, Fixture, 실제 교체 Owner(M3/M4/M5/M6/M9), API/Queue/Backup/Update 경계, 금지 임시 경로를 표로 기록한다.
- M2-03 Source, M2-04 Routing/Run, M2-06 AccountSecurity의 공개 Export를 재사용하고 기존 계약을 재작성하지 않는다. 필요한 연결은 최소 Export 추가와 전용 Adapter 조합으로 제한한다.
- R1-D022 Next Canary는 개발·GitHub Check·ysna-server 격리 검증 전용이며 운영 Release 금지 상태를 유지한다. Dependency·Lockfile·Toolchain을 변경하지 않는다.
- 실제 코드가 승인 설계와 충돌하면 증거와 선택지를 어울1에게 보고하고 쓰기를 중지한다.

## 5. 허용 변경 범위

- `apps/web/app/operations/`, `apps/web/app/notifications/` Prototype Route
- `packages/ui/src/`의 Operations/Recovery 전용 Model·Pane과 기존 Export 연결 최소 수정
- `scripts/tests/operations-recovery.test.mjs`와 기존 회귀 Test의 최소 수정
- `docs/01_architecture/operations_recovery_prototype_adapter_contract.md`
- `docs/03_evidence/release_1/R1-M2-07/`
- 지정 진행 기록과 결과보고

금지:

- 실제 API·Auth·DB·Migration·Queue·Worker·Object Storage·Model·Connector·Backup·Restore·Update·Rollback 구현 또는 외부 배포
- Dependency·Lockfile·Toolchain·CI 설정 변경과 R1-D022 완화
- M2-01~06 정본 계약 재작성, 무관 Refactor, 기존 Fixture 삭제
- 운영 자원·공유 DB·실데이터·서비스의 장애 주입 또는 파괴적 복구
- Secret·Credential·개인정보 원문, 내부 Host/Port 또는 성공으로 보이는 임시 Backend Mock

## 6. TDD와 구현 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| S0 | 정본·Hash·기준 SHA·Branch·단일 Writer 확인 | 진행 기록 |
| S1 | 상태·권한·자동/수동 Trigger·중복·Backoff·축소 운영 부정 Test 선작성 | 계약별 유효 RED |
| S2 | 순수 OperationsRecovery Domain Model·Reducer 최소 구현 | 전용 Model Test PASS |
| S3 | `/operations`·`/notifications` Route·Pane·상태 보존 연결 | Route·Navigation 회귀 PASS |
| S4 | Dashboard·Queue·Alert·보안 신호·Backup/Update Preview 구현 | TS-OPS-001~004 대응 PASS |
| S5 | `waiting_model` 자동·수동 새 Run·Idempotency·Backoff·Audit 구현 | TS-OPS-002A 직접 PASS |
| S6 | Daon/External/Local/Internet/Index/Evidence 장애→축소→복구 구현 | TS-OPS-010~016 대응 PASS |
| S7 | 네 폭 Production Browser 실제 클릭·Console/Network·접근성 증거 | PNG·Browser JSON |
| S8 | 전체 회귀·Lint·Build·공통 Gate·Adapter 계약·Evidence Manifest | 전부 PASS |
| S9 | 결과보고·Diff·허용 범위·진행 기록 최종 확인 | `COMPLETED` 또는 정식 보고 |
| S10 | 어울1 Commit·Push 후 GitHub·ysna-server exact SHA 검증 | Required Check·ARM64 PASS |

각 단계에서 기존 Green을 확인한 뒤 신규 계약 Test를 선작성하고 유효 RED를 확인한다. 구현 후 같은 Test를 Green으로 만들며 실패 Test 삭제·완화·Skip을 금지한다.

## 7. 필수 테스트와 증거

### 7.1 자동 테스트

- Service/Queue/Model/Node/Connector/비용/Backup/Update 상태 Projection
- Alert 중복 억제·세대 분리·읽음 상태·Operations Deep Link
- `auto`·허용 `local_only` Readiness Event 자동 Queue 1건
- `pinned`·직접 선택 자동 실행 0건과 권한 있는 수동 새 Run
- 권한 회수·다른 Tenant/Workspace·Payload Role/Grant 주입 거부
- 이전 Run 불변, 새 Snapshot·retry 계보·Trigger/Audit 완전성
- 활성 Run·중복 Event·Idempotency·Backoff 억제와 변경 0건
- Ready Gate 성공, 정책 후보 0, Runtime 재소진 세 경로
- Daon·External·Local·Internet·Index·Evidence 장애별 축소 불변조건
- 비용 한도 자동 재시도 0건, Step-up·AccessDecision 운영 신호
- 기존 M2-01~06 전용/선택 회귀, Workspace Lint, Production Build
- 공통 품질 게이트 전 범주 PASS·Failures 0·Exit 0

### 7.2 실제 Production Browser

새 Production Build에서 최소 1920×1080, 1200×900, 800×900, 500×900을 실제 검증한다.

- `/operations`와 `/notifications` 실제 Route·Screen ID, 404/정적 HTML 위장 없음
- 운영 상태 카드와 장애 여섯 종류의 경고·제한·복구 직접 문자열
- 자동 Queue·수동 Queue·중복 억제·Backoff·새 Run 계보 직접 문자열
- `pinned` 무단 전환 0건, Local→External 무단 전환 0건, Evidence 장애 승인/전달 차단
- Alert 중복 Count·읽음·Incident Deep Link·복구 상태 보존
- Backup/Restore/Update/rollback이 Preview이며 실제 실행 0건임을 직접 표시
- Console warning/error 0, same-origin 외 요청 0, 금지 내부 주소 0
- Keyboard/Focus/Tooltip/Escape/ARIA, 500px 문서 Overflow 0

PNG만으로 판정하지 않는다. `browser-validation.json`에는 클릭 순서, 직접 표시 문자열, Console, Network/Resource Timing, URL, Viewport, Prototype 미실행 항목을 함께 기록한다. Resource Timing을 사용할 수 없으면 `0`으로 추정하지 말고 unavailable 사유와 Source 정적 검사를 분리한다.

### 7.3 Evidence

- `docs/03_evidence/release_1/R1-M2-07/evidence-manifest.json`
- 네 폭 PNG와 `browser-validation.json`
- 자동/수동/중복 억제/장애 복구 Domain Evidence JSON
- Adapter 계약과 결과보고
- SHA-256·Byte·대상 Commit/환경·Mock/실제 실행 경계

## 8. 진행 기록과 결과보고 규칙

`docs/04_test_reports/release_1/R1-M2-07_progress.md`를 작업 시작 즉시 생성한다. 착수, 각 S 단계 완료, 오류 발생·원인·복구, 테스트 완료, 종료 직전에 시각·단계·상태·변경 파일·명령/테스트 결과·오류/원인/복구·다음 작업을 기록한다. 장시간 설치·Build는 Process 생존과 출력/mtime을 확인하며 기다리고, 시간 제한만으로 실패 처리하거나 검증 방법을 바꾸지 않는다.

결과보고는 `판정 → 판단 이유 → 조치` 순서로 작성한다.

- `COMPLETED`: 필수 산출물과 검증 증거가 모두 있고 미해결 C2/C3가 없음
- `FAILURE_REPORT`: 동일 `issue_id`, 실패 단계·재현·원인·시도한 대안·현재 Diff·안전 상태·필요 결정이 있는 정식 보고
- `INCOMPLETE`: 구현/증거 일부가 남았지만 정식 실패로 확정하지 않은 인계

단일 명령 실패, Tool Timeout, 권한·환경 문제, 예기치 않은 중단은 정식 실패 횟수로 세지 않는다. 원인을 조사하고 승인 경계 안의 대안을 적용한 뒤 같은 상태에서 이어간다. 어울2는 Commit·Push·PR·Merge·외부 배포를 수행하지 않는다.

## 9. 완료 조건

- `/operations`·`/notifications`가 Production-bound Prototype으로 직접 실행되고 네 폭에서 상태가 보존된다.
- 운영 상태, 경고, `waiting_model` 자동·수동 새 Run, 중복 억제, 장애별 축소·복구가 설계 불변조건과 일치한다.
- 권한·Step-up·정책·비용·근거 무결성 우회가 0건이며 Prototype이 실제 운영 성공을 위장하지 않는다.
- 전용·기존 회귀·Lint·Build·공통 품질 게이트와 실제 Browser 검증이 전부 통과한다.
- Adapter 계약·Evidence Manifest·진행 기록·결과보고가 완전하다.
- 관련 없는 파일 변경, Dependency/Lockfile/Toolchain 변경, 실제 파괴적 작업, 내부 주소/Secret 노출이 0건이다.


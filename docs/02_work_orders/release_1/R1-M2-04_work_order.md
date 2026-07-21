# R1-M2-04 작업지시서 — Run·모델·근거 흐름

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M2-04` |
| issue_id | `R1-M2-04-I001` |
| 작업 | Run·모델 선택·Routing·Fallback·Citation Production-bound Prototype |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| 기준 Branch | `codex/r1-m2-04` |
| 기준 SHA | `b9c6ff9b478a9c89c74e50d57deb279501d4ac3b` |
| 선행 작업 | `R1-M2-02`, `R1-M2-03` 완료·Merge |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M2-04_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-04_attempt-1.md` |

어울2는 착수 전에 아래 정본을 EOF까지 읽고 SHA-256을 대조한다. 요약본으로 대체하지 않는다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| `docs/04_test_reports/release_1_test_plan.md` | `C45DAE31FD408AF0D8885E006E570CC3BE36852A9F925811F8BC329C85ED9D13` |
| `docs/01_architecture/workspace_layout_state_adapter_contract.md` | `3E3A95C5299A2B68519A631DEC75CA03F71712B32DA1F43553CFCA434C2731C8` |
| `docs/01_architecture/source_authority_prototype_adapter_contract.md` | `F74E7E07DB9D93804C03DC3C887F5C570F3ACFAD6E1CCAFA3F7D44C29B59DA79` |

## 2. 목적과 사용자 완료 여정

사용자가 적응형 Workspace에서 실행을 시작하기 전에 모델 선택 Mode와 비용 한도를 확인하고, 실행 중 단계·모델 선택 이유·Fallback·비용·근거 상태를 이해하며, 정책 차단이나 사용자 판단이 필요한 경우 안전한 다음 행동을 선택할 수 있는 Production-bound Prototype을 완성한다.

최소 사용자 여정은 다음과 같다.

1. `auto`, `local_only(device_only | private_org_allowed)`, `pinned` 중 Mode를 선택한다.
2. 선택 Mode·역할·데이터 영역·정책 버전·비용 한도가 포함된 Frozen RoutingContext Preview를 확인한다.
3. 실행을 시작하고 `accepted → planning → retrieving → generating → validating → completed` 진행을 본다.
4. 선택 후보·제외 이유·최종 모델·ModelAttempt·Fallback 이유를 실행 결정 원장에서 확인한다.
5. Citation을 눌러 당시 SourceVersion·Evidence 위치·문맥을 기존 Evidence Viewer에서 연다.
6. 근거가 충분·부분·부족인지, 중요 충돌 때문에 검토가 필요한지 확인한다.
7. `waiting_user`, `policy_blocked`, `failed`, `cancelled` 분기를 선택하고 재시도 가능 여부와 다음 행동을 확인한다.
8. 비용 한도 도달 시 `policy_blocked/COST_LIMIT_EXCEEDED`, 미완성 결과 미전달, 동일 Frozen Context 자동 재시도 금지를 확인한다.

## 3. 구현 범위

### 3.1 Run 상태와 화면

- `RunViewState`를 Workspace 정본 상태에 추가하고 Pane 언마운트·폭 전환 뒤에도 보존한다.
- 정상 상태 6단계와 분기 상태 `waiting_user | waiting_approval | policy_blocked | failed | cancelled`를 구분한다.
- 현재 단계, 완료 단계, 중단 단계, Trace ID, 시작 시각, 취소 가능 여부를 비색상 표식과 텍스트로 표시한다.
- Prototype 상태 전이는 결정론적 Fixture와 순수 Reducer로 실행한다. 실행되지 않은 실제 API·DB·LLM 호출을 성공으로 위장하지 않는다.

### 3.2 모델 선택과 Frozen RoutingContext

- `auto`, `local_only.device_only`, `local_only.private_org_allowed`, `pinned`을 사용자 Control로 제공한다.
- Browser 상태에는 Raw Provider URL·Code·Secret을 넣지 않고 불투명 Deployment ID와 안전한 표시 이름만 사용한다.
- 실행 시작 시 Actor·Tenant·Workspace·Mode·역할·데이터 분류·영역·외부 전송 정책·고정 모델·지식/RuleSet Snapshot·정책 버전·기한·비용 한도/통화/적용 범위를 Snapshot한다.
- 시작 시 허용 후보 집합·결정론적 정렬 순서·Fallback 계획과 Prompt·Tool 계약 버전을 RunSnapshot에 고정한다.
- 사용한 Source·SourceVersion과 권위·가중치 적용 계층·요청값·유효값·Clamp 결과를 M2-03 정본에서 Snapshot한다.
- 실행 중 설정 변경은 현재 RunSnapshot을 바꾸지 않고 다음 Run에만 적용한다.

### 3.3 Routing과 Fallback 계약

- Hard Filter 5종과 Runtime Readiness Filter 4종을 정책 제외·Runtime 제외 Code로 구분해 표시한다.
- 승인 후보는 설계서 §10.4의 정렬 순서와 stable deployment ID Tie-breaker로 결정론적으로 선택한다.
- `auto`는 Timeout·Rate Limit·일시 장애·용량 부족에만 같은 Frozen Policy·역할·영역·Egress 범위의 다음 후보로 Fallback한다.
- `auto`의 정책 후보는 있으나 Runtime Ready 후보가 0개이거나 일시 장애가 모두 소진되면 재시도 가능 `failed/NO_AVAILABLE_DEPLOYMENT`로 종료한다. Source 의미 이해 역할은 `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`로 구분한다.
- 인증 오류·잘못된 요청은 다른 Provider로 우회하지 않고 재시도 불가 `failed`로 종료한다. 정책 차단·외부 전송 거부도 다른 후보로 우회하지 않는다.
- `pinned`의 Offline·Health·Capacity는 무단 모델 변경 없이 `waiting_user`와 `재시도 | 허용 모델 변경` 선택지를 표시한다.
- Local-private에서 External API로 자동 전환하지 않고, Stream 일부 출력 뒤 다른 모델로 이어 쓰지 않는다.
- 자동 Attempt와 사용자에게 제안한 전환을 결정 원장에서 구분한다.
- `waiting_approval`은 정책상 실행 전 별도 승인이 필요한 Run에만 사용하며 M2-05 OutputVersion 승인 대기로 사용하지 않는다.

### 3.3.1 실행 결정 원장

정상·Fallback·차단 Fixture 모두에서 설계서 §10.6과 TS-MDL-040의 필드를 누락 없이 표시·보존한다.

- Mode·RoutingPolicyVersion·후보·정책/Runtime 탈락 이유·선택 이유
- Provider Profile·Deployment·Model Artifact·Digest와 역할별 최종 모델
- 의미 이해 Vision/LLM과 Parser·OCR·Document Parse 보조 도구·버전
- 보조 도구 사용 이유·교차 검증 불일치·보완·검토 결과
- 비용 한도·통화·누적/예상 비용·비용 차단 시점
- 시작 시 허용 후보·정렬 순서·Fallback 계획과 실제 ModelAttempt 결과
- 데이터 영역·EgressDecision·지식/RuleSet Snapshot
- Node·Actor·Tenant·Workspace·Trace·Request·Run ID
- Token·Byte·지연·비용 사용량, Prompt·Tool 계약 버전

### 3.4 비용 한도와 안전 오류

- Attempt 전 누적 비용과 보수적 예상 비용을 한도·통화와 함께 표시한다.
- 한도 도달·다음 Attempt 초과 확실 시 새 호출 없이 `policy_blocked/COST_LIMIT_EXCEEDED`로 종료한다.
- 동일 Frozen Context 자동 재시도 Control을 제공하지 않고 미완성 출력은 결과 Pane에 노출하지 않는다.
- 권한 있는 사용자가 한도·정책을 변경하면 기존 Run은 불변으로 남기고 새 Run 생성 진입만 제공한다.
- 안전 오류는 Code·사용자 설명·실패 단계·영향 범위·재시도 가능 여부·사용자 조치·Trace ID만 표시하며 내부 Host·Stack·Secret·Provider 원문 오류를 노출하지 않는다.

### 3.5 Citation·근거·충돌

- Citation은 당시 `SourceVersionId`, `EvidenceId`, 위치, 문맥, 인용과 RunSnapshot 연결을 보존한다.
- 기존 M2-02 Evidence Viewer와 M2-03 Source-Version-Evidence 계약을 재사용하고 별도 Viewer 정본을 만들지 않는다.
- 근거 상태 `sufficient | partial | insufficient`를 비색상 텍스트·Icon으로 표시한다.
- 해결되지 않은 `material | critical` 지식 충돌은 `IMPORTANT_KNOWLEDGE_CONFLICT`와 검토 진입을 표시하고 최종 결과 확정을 차단한다.
- Citation·Evidence 연결 변경, 실제 검색·생성·Index·Provider 실행은 범위 밖이다.

### 3.6 Production-bound Adapter 계약

- Prototype Fixture와 실제 구현 교체 경계를 `docs/01_architecture/run_model_evidence_prototype_adapter_contract.md`에 기록한다. M3는 Client Shell, M4는 API/BFF 계약, M5는 저장 정본, M6-02·09·10·14는 실제 Routing·Provider·Citation·Run E2E 책임으로 구분한다.
- Domain 상태·Reducer·표시 Component와 향후 BFF/API Adapter를 분리한다.
- Browser는 same-origin 상대 경로만 사용할 수 있다. 이번 Prototype에서 실제 Network가 없으면 API 요청 0건을 증거에 명시한다.

## 4. 범위 밖

- 실제 Provider·LLM·Local Runtime·Daon Connector 호출
- Backend/API/BFF/Auth/DB/Object Storage·Migration·Secret 구현
- 실제 Token Streaming·SSE·비용 청구·모델 다운로드·배포·Local Node Pairing
- 실제 Retrieval·Index 생성·Citation 조정 또는 SourceVersion 변경
- M2-05 Studio 생성·편집·승인 수명주기
- 새 Dependency·Lockfile·공개 API·데이터 계약·보안 경계 변경

범위 밖 기능은 `Prototype · unavailable`로 정직하게 표시한다. 성공 Toast·완료 상태·가짜 Network를 만들지 않는다.

## 5. TDD와 완료조건

Production 수정 전에 승인 계약을 재현하는 실패 Test를 작성하고 Red를 확인한다. 최소 Test는 다음을 포함한다.

- 4개 Mode의 후보 제한과 Raw Provider/URL/Secret 0건
- Frozen RoutingContext 불변과 다음 Run 반영
- Source/SourceVersion·권위·가중치 계층·요청/유효값·Clamp 결과 Snapshot 불변
- Hard/Readiness 제외 Code 구분, 결정론적 정렬
- 허용된 `auto` Fallback과 금지 사유 우회 0건
- `pinned → waiting_user`, Local-private→External 0건, Stream 이어쓰기 0건
- 정상 Run 6단계와 5개 분기 상태
- `waiting_approval`은 실행 전 승인 전용이고 OutputVersion 승인 대기로 사용하지 않는 경계
- `COST_LIMIT_EXCEEDED`, 동일 Context 자동 재시도 0, 미완성 결과 0
- Citation→기존 Evidence Viewer의 SourceVersion·위치·문맥 일치
- 중요 충돌 최종화 차단과 근거 충분/부분/부족
- M2-02·M2-03 전체 회귀, 반응형 상태 보존, 접근성·Tooltip·Focus
- Browser 금지 URL·`fetch`·`NEXT_PUBLIC_API_BASE_URL` 0건

### 5.1 TS-MDL 추적 경계

| 검증 시점 | 이 작업에서 검증 | 후속 실제 검증 |
| --- | --- | --- |
| M2 Prototype | TS-MDL-001~004 Mode UI/Reducer, 010~013 Filter·정렬·Frozen Snapshot, 020~027 Fallback·종료 상태 Fixture, 030~032 비용 차단, 040 원장 필드, 041 불변 상태, Citation·충돌·접근성 표시 계약 | 실제 Provider·Network·Packet·서버 Audit는 통과로 선언하지 않는다. |
| M4~M6 | Prototype Adapter 교체 계약·불투명 ID·same-origin 경계를 인계 | TS-MDL-005~006 API 검증, 014 역할별 실제 호출, 020~032 실제 오류 주입·ModelAttempt·Network, 041 API 수정 거부, 042 UI-Route-Network-Audit 일치, M6-09 Citation 원문 재현, M6-10 E2E, M6-14 전체 Routing |

M2 Evidence에는 `prototype_contract_passed`와 `runtime_not_executed`를 함께 기록한다. 실제 API·Provider·Network·DB가 없는 Fixture 결과를 L4 실제 실행 PASS로 보고하지 않는다.

완료 시 다음을 모두 통과한다.

- 신규·기존 Workspace Test, Lint, Foundation, Toolchain, Independence
- Next Production Build와 공통 7범주 Quality Gate
- 실제 Production Browser 1920×1080, 1200×900, 800×900, 500×900 클릭 검증
- Console warning/error 0, 실제 API/비동일 Origin/금지 주소 0
- Screenshot·Browser JSON·SHA-256 Manifest·Architecture 계약·결과보고
- `git diff --check`, 추적 삭제 0, Lockfile 변경 0, 허용 범위 밖 변경 0

## 6. 실행 단계

| 단계 | 작업 | 완료조건 |
| --- | --- | --- |
| S0 | 정본·Hash·기준 SHA·보호 Dirty·선행 계약 확인 | 단독 Writer·허용 범위 기록 |
| S1 | 사용자 여정·상태·Routing·Fallback·Citation 실패 Test | Production 무수정 Red |
| S2 | Run·RoutingContext·Deployment·Decision 순수 모델 | 정상·분기·비용 계약 Green |
| S3 | 모델 선택·진행·결정 원장·안전 오류 UI | 활성 Prototype Control 연결 |
| S4 | Citation·근거·중요 충돌과 기존 Viewer 연결 | 계보·차단·Focus 계약 Green |
| S5 | 적응형·Keyboard·Tooltip·비색상 상태 완성 | 네 폭·접근성 PASS |
| S6 | Architecture Adapter 계약과 전체 자동 Gate | Build·7범주 Gate PASS |
| S7 | 실제 Production Browser 네 폭 검증 | 사용자 여정·Console·Network PASS |
| S8 | Evidence·Hash·Diff·결과보고 | `HANDOFF_READY` |
| S9 | 어울1 Commit·Push 후 GitHub·ysna-server 불변 SHA 검증 | Required Check·Artifact·ARM64 PASS |
| S10 | 최종 Evidence·정식 결과보고 | 완료조건 전수 대조 |

## 7. 허용 변경과 보호 범위

허용 범위는 Run·모델·근거 Prototype에 필요한 기존 `packages/ui`, `apps/web` 연결, 관련 Test, Architecture, Evidence, Progress, Report로 제한한다. 실제 필요 파일은 S0에 증거와 함께 기록한다.

- `package.json`, `package-lock.json`, Toolchain·CI·독립성 정책은 변경하지 않는다.
- M2-01/M2-02 Layout·Token·Route·Modal·Focus 계약과 M2-03 Source 권위·가중치·RuleSet·충돌 계약을 보존한다.
- 기존 `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`, `violations.json`의 EOL-only Dirty를 수정·복원·Stage하지 않는다.
- 다른 작업자와 병렬 코드 수정하지 않는다.

S9 서버 검증은 `/home/ubuntu/deploy/daon-user/R1-M2-04/<exact-push-sha>` 격리 Checkout으로 제한한다. 기존 `shared-db`, `common`, `netdata`, `proxy`를 사용하거나 변경하지 않는다. 기존 Container·Network·Volume의 사전·사후 Hash 일치, 정확한 detached SHA와 Clean 상태, ARM64 Build·Test·Gate, 임시 자원 0을 증거로 남긴다. Schema·Migration 신호가 없으면 `NOT_APPLICABLE_NO_SCHEMA`와 DB 명령 0건을 기록하고 임의 DB를 만들지 않는다.

## 8. 진행 복구 기록과 보고

어울2는 착수, 각 단계 완료, 오류·복구, Test 완료, Browser 검증, 종료 직전에 진행 파일에 다음을 기록한다.

`시각 | 단계 | 상태 | 변경 파일 | 명령·Exit | 검사 결과 | 오류·원인 | 복구·대안 | 증거 경로 | 남은 위험 | next_action`

장시간 설치·Build·서버 명령은 충분히 기다린다. 첫 오류만으로 실패보고하지 않고 원인과 승인 범위 안 대안을 조사한다. 결과보고는 다음 형식을 지킨다.

`status | issue_id | 수행한 작업 | 생성·변경 결과 | Test | Browser 증거 | 미해결 사항 | 다음 판단`

검토 출력은 `판정 → 판단 이유 → 조치` 순서다. S8 이후 구현 쓰기를 중지하고 어울1의 검토·Commit·Push를 기다린다.

## 9. 승인 경계

승인된 설계 안의 내부 구현 방법은 어울1 판단 범위다. 기능 범위·요구사항·공개 API·데이터 계약·보안 경계·중요 위험을 변경해야 하면 즉시 쓰기를 중지하고 증거와 선택지를 보고한다. Commit·Push·PR·Merge·외부 배포는 어울1이 수행한다.

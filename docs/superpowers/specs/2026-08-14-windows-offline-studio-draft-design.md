# Windows Offline Studio 초안 설계

## 1. 상태와 목표

`R1-M8-10`은 Windows Local-private Workspace에서 네트워크가 차단된 상태로 업무 문서 초안을 생성·편집하고, 암호화 저장소에 실행 계보와 동기화 대기 상태를 보존한 뒤, 재연결 시 사용자가 승인한 항목만 Cloud-sync로 전달하는 작업이다.

선행 계약은 다음과 같이 완료되어 있다.

- `R1-M7-02`: Local-private Source와 Managed Local Model만 사용하는 Offline 질문·근거 계약
- `R1-M8-01`: `configuring → confirmed → submitted` 생성 설정과 불변 `GenerationSettingsSnapshot`
- `R1-M8-06`: Template·Section·Evidence·검토 상태를 갖는 업무 문서 초안
- `R1-M5-03`: SQLCipher Metadata·AEAD File Store·Canonical Envelope·OS Secure Store Key
- `R1-M5-05`: Preview·Step-up 승인 Snapshot·암호화 Offline Queue·재개 Batch·충돌 명시 선택

목표는 이 계약을 새 정본이나 우회 계층으로 복제하지 않고 Windows Local Service와 Tauri 화면 흐름으로 결합하는 것이다.

### 1.0 구현 우선순위 교정

최종 Workspace Shell은 승인·배포된 상태를 기준으로 유지한다. 이후 구현은 신산님이 2026-08-14 확정한 `공통 기반 모듈·공통 API → 메뉴별 수직 기능` 순서를 따른다. 먼저 여러 화면이 함께 사용하는 인증·권한·Canon·Provider/Model·Knowledge Context·Citation·Output Version·Audit·same-origin BFF 계약을 완성하고 실제 저장·오류·보안 동작을 검증한다. 그 다음 승인된 화면 구조를 바꾸지 않고 메뉴를 하나씩 `Domain → Repository → API/BFF → UI → 실제 Browser` 순서로 닫는다.

### A1 내부 운영 Audit 내구성 결정 (2026-08-14)

공개 API·DTO·사용자 데이터 계약은 변경하지 않는다. 기존 `audit_events`는 Cloud notification/Canon 용도와 `workspace_id NOT NULL` 제약을 가지므로 Identity Session·ACL·Step-up의 workspace 없는 정본 `AuditEvent`를 혼합 저장하지 않는다. Alembic head `0015`는 전용 append-only security audit를 추가한다. tenant/workspace nullable scope, actor/action/target/policy/trace/outcome/safe-code와 이전·현재 hash를 무손실로 저장하고 UPDATE/DELETE를 trigger·RLS/FORCE RLS·least privilege로 거부한다. Step-up issuance/consumption idempotency는 Identity 저장소의 schema-versioned 원장 하나를 권위 저장소로 사용해 발급·소비 상태와 원자적으로 갱신하며, PostgreSQL에 사용되지 않는 이중 원장을 만들지 않는다. fresh/upgrade/rollback/reapply, PostgreSQL 15/18, cross-tenant read/write0, restart recovery는 A1 actual Gate다.

Step-up same-key replay의 raw authorization은 평문·ciphertext로 저장하지 않는다. production은 root-owned `DAON_STEP_UP_TOKEN_KEY_FILE` reference의 전용 key를 읽고 `tenant/actor/idempotency-key/request-fingerprint/grant identity/key version` domain-separated HMAC으로 raw grant를 재구성한다. ledger에는 fingerprint·grant metadata·token digest와 key id/version만 남긴다. key reference가 없거나 rotation pending replay가 검증되지 않으면 fail-close한다.

OutputVersion은 내용 계보와 상태 전이 잠금을 분리한다. `content_version`은 동일 StudioOutput 안의 immutable v1→v2→v3 및 `previous_version_id` 검증에만 사용하고, 기존 `version`은 `transition_canon_state`의 상태 전이 낙관적 잠금에만 사용한다. 최신 Version 조회는 `content_version DESC`로 결정하며 same-key 동시 생성은 transaction advisory lock 뒤 replay를 먼저 읽는다. 다중 내용 Version이 존재하면 이를 표현할 수 없는 0015 이하 downgrade는 `OUTPUT_VERSION_DOWNGRADE_BLOCKED`/SQLSTATE 55000으로 차단한다.

최종 화면 단계에서는 가짜 연결·가짜 성공·Fixture 통계를 표시하지 않는다. 아직 연결되지 않은 기능은 완성된 시각 구조 안에서 `미설정 | 연결 필요 | 준비 중`으로 정직하게 표시하고 Action을 fail-close한다.

### 1.1 제품 목표와 이중 입력 원칙

최종 제품 목표는 단순한 Local LLM 실행기가 아니라 `신뢰 가능한 지식과 사용자의 Source → 선택한 LLM → Citation이 결속된 답변·업무 산출물`을 제공하는 Grounded Knowledge Studio다. NotebookLM의 Source-grounded 흐름을 출발점으로 삼되, Daon은 지식의 권위·최신성·충돌·검토 상태와 산출물의 Version·승인·Sync까지 운영 계보로 관리한다.

입력은 처음부터 두 종류를 모두 허용한다.

- **Daon 지식**: Daon2·Daon2.5·Daon3에서 생성되고 검토·등록된 지식이 기본·우선 입력이다.
- **사용자·외부 지식 Source**: 사용자가 직접 추가하거나 Connector로 연결한 문서·웹·표·이미지·음성·영상·DB/API Projection 등 모든 지식 Source를 사용할 수 있다. 다만 검증되지 않은 입력을 등록 지식과 동일한 품질로 가장하지 않고 Source·처리·검토·`unverified` 상태를 표시한다.

Daon 사용자 프로그램은 독립 제품 원칙을 유지한다. Daon2·2.5·3의 내부 DB·Module·File Path를 직접 참조하지 않고, Version·Digest·Producer·Authority·Review·Citation 계보가 있는 표준 Knowledge Package를 Connector와 명시 `KnowledgeRegistration` 경계로 수신한다. Raw Source는 자동으로 Daon 지식으로 승격하지 않는다.

사용자·외부 지식 Source의 실제 오프라인 사용은 화면에 보이는 Cloud Source ID만 복사하는 방식이 아니다. Daon은 Source 형식을 허용 목록으로 제한하지 않고 current Native Session Workspace에 결속한 원본을 지식으로 등록·보존한다. 형식별 수집 Adapter는 가능한 경우 원본에서 bounded Evidence Item과 LLM별 입력 표현을 만들며, PDF·plain text·Markdown Adapter는 최초 구현 순서일 뿐 Knowledge 계약의 허용 목록이 아니다. 이후 웹·표·이미지·음성·영상·DB/API Adapter를 추가해도 Knowledge Context·Retrieval·Provider·Citation 계약은 바꾸지 않는다. 선택 LLM이 원본 또는 생성된 표현을 처리할 capability가 없을 때만 해당 Run을 `MODEL_INPUT_CAPABILITY_UNAVAILABLE`로 fail-close하며 Source 자체를 목록·지식에서 제외하지 않는다. Parser·OCR·전사·구조화 처리는 승인된 Local Adapter 안에서만 수행하며 Cloud API·외부 Provider·임의 subprocess로 우회하지 않는다. SourceVersion·원본 digest는 Workspace에 결속된 append-only Canon으로 항상 보존하고, IndexVersion·EvidenceSpan은 생성 가능한 표현과 그 변환 계보를 별도 보존한다.

두 입력은 불변 `KnowledgeContextSnapshot`으로 함께 고정한다. Snapshot은 `daon_priority | mixed | raw_only` mode, 각 Item의 `daon_knowledge | raw_source` origin, producer product/version, SourceVersion 또는 KnowledgeRegistration/OutputVersion, Authority·Weight·Freshness·Conflict·Review 상태와 digest를 보존한다. Retrieval은 Daon 지식을 기본 우선하되 Raw Source의 상충 근거를 숨기지 않고 Citation에 origin을 표시한다.

LLM은 상단 `설정 → LLM 설정`에서 관리하고 화면에서 선택할 수 있어야 한다. 공통 설정 화면은 `CEREBRAS | GROQ | MISTRAL | OPENAI | UPSTAGE | GEMINI | OPENROUTER | ANTHROPIC | OLLAMA` Provider의 Profile·Endpoint·Model·Credential 설정 여부·연결 상태를 동일 계약으로 표시한다. Credential 원문은 저장 후 다시 표시하지 않고 Server Secret Reference로만 결속한다. 선택 결과는 표시 이름이 아니라 Provider code/kind, Provider profile, deployment, model ID/digest와 생성 설정이 결속된 공통 `ModelSelectionSnapshot`으로 고정한다.

오프라인 목록은 현재 Ollama에 실제 설치되어 있고 `/api/show`의 capabilities에 `completion`이 있으며 조직이 승인한 Deployment와 exact model name/digest가 일치하는 모델만 포함한다. `:cloud` 모델, remote host가 필요한 모델, embedding 전용 모델은 오프라인 생성 후보에서 제외한다. 선택한 모델이 사라지거나 digest가 바뀌면 자동으로 Groq·Upstage 또는 다른 Ollama 모델로 대체하지 않고 fail-close한다.

실제 생성 기능·품질 검증은 신산님 지시에 따라 `UPSTAGE | GROQ | MISTRAL` 중 어울이 선택한 대표 Provider **하나**로 수행한다. Provider 호환성 문제가 의심될 때만 두 번째 Provider를 추가하며 세 Provider 전체에 동일 기능 시험을 반복하지 않는다. 신산님의 수용 시험 Provider 선택은 어울의 개발 시험 선택과 독립이다. 나머지 Provider는 설정·인증·모델 조회·Health/Readiness 연결 계약까지만 확인한다.

## 2. 선택한 방식

기존 경계를 조립하는 `Local-first + approved sync` 방식을 사용한다.

1. Desktop UI는 Tauri command만 호출한다.
2. Tauri는 command-bound 단기 Token으로 Loopback Local API를 호출한다.
3. Local Service는 Daon 지식 우선·Raw Source 선택을 고정한 Knowledge Context와 사용자가 선택한 Ollama Deployment로 오프라인 초안을 만든다. Groq·Upstage Adapter와 자격정보는 Cloud API 내부에만 존재하며 Desktop·Local Service로 전달하지 않는다. 이번 Online actual Gate는 정제 synthetic Knowledge Context의 Adapter·Schema·Citation 검증 범위이고, 새 Cloud Studio 생성 공개 API를 추가하지 않는다.
4. 초안·설정·RunSnapshot·OutputVersion·PendingOperationReference를 기존 SQLCipher Canon Envelope에 append-only로 저장한다.
5. 오프라인에서는 편집과 Sync Draft Queue 보존까지만 허용한다.
6. 재연결 후 Tauri Native Cloud Client가 기존 Sync 공개 API를 사용해 Preview와 사용자 승인을 수행한다.
7. 현재 Session·Membership·정책·Version·Step-up이 유효한 승인 항목만 전송한다.

Desktop 전용 평문 Queue, 별도 SQLite, Browser fetch, Cloud 우선 생성, 오프라인 승인 성공 위장은 사용하지 않는다.

## 3. 사용자 화면과 운영 흐름

### 3.1 화면 구조

Windows Workspace는 현재의 1920×1080 3열 구조와 기능 위치를 유지한다. 화면을 새 Dashboard나 별도 Studio Cockpit으로 재배치하지 않는다.

- **왼쪽 — Source·지식·권위**: Local Source 등록·선택·준비 상태와 근거 범위
- **가운데 — 대화·실행**: 근거 질문, Citation, 초안 편집 시 Section 제목·본문·Evidence·`unverified` 경고와 저장 상태
- **오른쪽 — 업무 Studio**: 산출물 유형, 생성 설정, Version·검토 조건, 저장된 산출물과 Sync 대기 상태

초안 편집을 시작하면 가운데 패널의 대화 본문이 Draft Editor로 전환된다. 왼쪽 Source와 오른쪽 업무 Studio의 위치·폭·의미는 유지한다. 오른쪽에는 생성 설정·근거 점검·Version 기록·검토 조건·Sync 대기함을 단계별 내부 View로 표시하고 한 화면에 모든 Form을 펼치지 않는다.

### 3.1.1 시각 디자인 언어

NotebookLM의 `Sources · Chat · Studio` 작업 위계와 절제된 도구 배치를 참고하되 Google 제품을 복제하지 않고 Daon 고유 표현으로 구현한다. 확정 시각 방향은 `NotebookLM-inspired Violet`이다.

화면의 구체적 기준은 다음과 같다.

- **App Bar**: Workspace 식별·안전 상태는 왼쪽, `운영상태`와 `설정`은 오른쪽에 둔다. 설정 Button은 NotebookLM처럼 현재 화면 위에 정렬된 Menu를 열고 그 안에 `LLM 설정`을 하나의 명확한 항목으로 제공한다.
- **Source Panel**: 파일명 Bullet 반복이 아니라 선택 상태·종류·처리 상태·권위·Version이 구분되는 조밀한 List Row를 사용한다. 등록 Action과 목록을 분리하고 Empty/Loading/Error를 같은 높이 체계로 표시한다.
- **Conversation Panel**: 상단에는 짧은 Context/Citation 상태, 중앙에는 대화·답변·Citation, 하단에는 고정 Composer를 둔다. 질문과 추가 인증 입력을 한 줄에 억지로 나열하지 않으며 추가 인증은 필요한 순간에만 별도 단계로 연다.

#### Cloud Question Knowledge Context 호환 확장

기존 단일 Source 질문 계약은 호환 유지한다. Web의 새 질문은 동일 `/api/v1/workspaces/{id}/questions`와 `/questions/authorization`에 `knowledge_context`를 전달하며 별도 우회 Endpoint를 만들지 않는다. `knowledge_context`는 `mode: daon_priority | mixed | raw_only`와 bounded `resources[{resource_kind,resource_id,version_id?}]`를 exact DTO로 받는다. `resource_kind`는 Resolver Registry의 safe code이며 현재 `source | knowledge_package`를 구현하고 이후 문서·웹·media·DB/API 유형은 동일 배열에 Resolver만 추가한다. legacy `source_id/source_version_id` 쌍과 `knowledge_context`는 동시에 보낼 수 없다.

서버는 승인·등록·유효기간이 확인된 Knowledge Package와 사용자가 선택한 Source를 형식 독립적인 `EvidenceResource`로 해석해 불변 `KnowledgeContextSnapshot`에 저장한다. `EvidenceResource`는 PDF·문서·일반 Text·Markdown·웹·표·이미지·음성·영상·DB/API Projection·Daon 생성 Knowledge Snapshot 등 현재와 향후 입력 종류를 제한하지 않는다. 수집 Adapter는 원본을 검색·추론 가능한 bounded Evidence Item으로 정규화하고, LLM 입력은 출처 형식이나 `daon_knowledge | raw_source`에 따라 별도 제한·분기하지 않는다. Text인 Daon 생성 지식은 다른 Text Evidence와 동일하게 검색·추론된다.

`origin`, producer/version, resource/version ID, digest, authority와 locator는 LLM 능력을 제한하는 값이 아니라 계보·권위·충돌·Citation 표시를 위한 메타데이터다. Snapshot은 mode와 모든 Resource/Item을 RunSnapshot에 결속하고 검색·Provider payload·추가 인증 fingerprint는 동일 Snapshot 전체를 사용한다. Citation은 공통 `EvidenceResource` 계보와 `context_item_id`, 형식별 opaque locator를 반환한다. 원문 열기는 LLM 질문 계약과 분리된 Resolver가 담당하며 PDF page, 문서 구간, 웹 문단, 표 범위, media timecode, Knowledge Snapshot 구간 등을 같은 Citation 인터페이스로 해석한다. 지원되지 않는 Renderer가 있어도 해당 지식을 LLM 입력에서 제외하거나 다른 Source로 바꾸지 않고 `CITATION_RENDERER_UNAVAILABLE`로만 fail-close한다. legacy 요청은 동일 공통 계약의 단일 Resource로 투영한다.
- **Studio Panel**: 상단은 NotebookLM Studio처럼 3열 생성 유형 Tile Grid, 하단은 저장된 산출물 통합 Library다. Tile은 16px Icon·짧은 이름·Chevron·절제된 서로 다른 Surface tint를 사용한다. 현재 기능은 활성, 후속 기능은 Phase와 `준비 중`을 명시한 disabled 상태다.
- **산출물 Library**: 유형 Icon, 제목, 사용 Source 수, Version, 생성시각, 상태와 More Menu를 한 Row에 표시한다. Audio/Video처럼 재생 가능한 유형만 Play Action을 표시하고, 생성 결과가 없으면 목적이 분명한 Empty State를 사용한다.
- **설정 Menu·Modal**: Menu는 현재 화면을 밀어내지 않는 anchored floating surface다. `LLM 설정` Modal은 Provider Card Grid와 선택 Provider 상세 영역으로 나누며, 긴 단일 Form이나 9개 Provider Form 동시 노출을 금지한다.

- Canvas는 연한 중립색, 각 패널은 불투명 Surface와 얇은 Border·13px Radius·절제된 Shadow를 사용한다.
- Accent는 Violet 단일 계열로 선택·주요 Action·Citation·활성 상태에만 사용한다. 모든 Button·Panel을 색칠하지 않는다.
- 제목 16px, Panel 제목 14px, 기본 본문·Form 12px, 보조 설명 10px, 아주 작은 보조 9px 기준을 유지한다.
- Panel 제목에는 의미가 있는 16px Line Icon과 짧은 Label을 사용한다. 장식용 대형 Illustration·Gradient·과도한 Badge는 사용하지 않는다.
- Primary Action은 Panel당 하나만 채움 Button으로 두고 나머지는 Secondary·Ghost 강도로 구분한다.
- Source 선택, Citation, Version, Sync 상태는 색상뿐 아니라 Icon·Text·Shape를 함께 사용해 구분한다.
- 빈 공간은 의미 있는 Empty State·최근 항목·작업 상태로 사용하며 가짜 통계나 장식용 Card를 만들지 않는다.
- 긴 설명 Box의 상시 노출을 금지한다. 필수 Label은 화면에 두고 추가 설명은 `i` Icon Tooltip·Popover로 제공한다.
- 오류는 하단의 거대한 고정 Box가 아니라 관련 Panel의 Inline Alert 또는 상단 상태에서 접근하는 상세 팝업으로 표시한다. Safe Error Code와 사용자가 할 조치만 노출한다.

Light·Dark Theme 모두 동일한 위계와 WCAG AA 대비를 유지한다. 애니메이션은 Panel 전환·Popup 등장에 150~200ms 이하로 제한하고 `prefers-reduced-motion`을 존중한다.

기존 화면처럼 브라우저 기본 Control, 파일명 Bullet 반복, 전체 폭 설명 Box, 하단 전역 오류 Box, 내부 `WORKSPACE_POLICY | WEIGHT_PROFILE | RULESET_BINDING` 문자열, 좁은 입력을 세로로 길게 쌓은 설정 Form을 최종 화면에 남기지 않는다.

### 3.1.2 운영상태·설정 팝업

상단 App Bar에는 작은 전체 상태 표시와 `운영상태`, `설정` Button을 둔다. 상세 정보는 기본 3열 작업 화면을 밀어내지 않고 별도 Modal Popup으로 연다.

- **상시 상태**: 정상·주의·오류 중 하나와 Offline·Cloud 연결 여부만 짧게 표시한다.
- **운영상태 Popup**: Local Service, 암호화 저장소, Ollama 연결·선택 모델, Cloud Sync, 대기 작업, 마지막 확인 시각과 안전한 조치를 표시한다.
- **설정 Popup**: NotebookLM과 같은 단일 설정 메뉴 안에 `LLM 설정` 진입 항목을 두고, 별도 LLM 설정 View에서 Provider Profile·Credential 설정 여부·Endpoint·Model·연결 테스트·기본 모델을 관리한다. 기본 출력 형식, Version 저장 방식, Sync 승인 방식은 같은 설정 체계의 별도 Section으로 둔다.
- 조직 강제 RuleSet·검토 조건·Egress 정책은 읽기 전용으로 표시하며 이 Popup에서 해제할 수 없다.
- 설정 변경이 있으면 저장·취소를 명시하고, 미저장 변경이 있는 상태에서 닫을 때 확인한다.
- Modal은 `role=dialog`, `aria-modal=true`, 제목 연결, 최초 Focus, Tab Trap, Escape·닫기, Background inert와 Focus 복귀를 구현한다.
- 오류·상태 응답에는 내부 URL, Local Port, Token, Path, Stack, SQLSTATE를 포함하지 않는다.

Critical 오류는 Popup 안에만 숨기지 않는다. 상단 상태가 `주의 | 오류`로 바뀌고 해당 Panel의 Action을 fail-close한 뒤 사용자가 운영상태 Popup에서 상세 조치를 확인한다.

### 3.1.3 향후 화면 적용 기준

향후 Windows Workspace에 추가되는 화면은 별도 시각 체계를 만들지 않고 이 설계의 App Bar, Violet Accent, Surface, Panel Header, Button 위계, Inline Alert, Modal Popup, 상태 표시와 Focus 계약을 재사용한다. 이 Work Order는 무관한 기존 화면 전체를 일괄 재설계하지 않는다. 새 화면과 이번 범위에서 직접 수정하는 화면부터 적용하고, 기존 화면은 해당 기능을 후속 변경할 때 같은 기준으로 정렬한다.

### 3.2 정상 흐름

```text
입력 모드 선택: Daon 지식 우선 | 혼합 | Raw Source만
→ Knowledge Context 확인
→ 적격 LLM 선택(Offline: Ollama, Online Gate: Groq 또는 Upstage)
→ 설정 입력
→ 설정 확인
→ 불변 GenerationSettingsSnapshot 저장
→ 선택 Provider 초안 생성
→ RunSnapshot·StudioOutput·OutputVersion 저장
→ Section 편집 시 새 OutputVersion append
→ 동기화 대상 선택
→ PendingOperationReference와 Queue draft 저장
→ 재연결 감지
→ Sync Preview 확인
→ Step-up 포함 명시 승인
→ 현재 권한·정책·Version 재검증
→ 승인 항목만 Batch 전송
→ reindex_requested 또는 conflict 표시
```

초안 생성과 편집은 네트워크 상태와 무관하게 Local Service에서 완료된다. 재연결은 자동 전송의 트리거가 아니라 사용자에게 Preview 확인이 가능해졌음을 알리는 상태 변화다.

### 3.3 실패·대체 흐름

- Ollama Service 또는 선택 모델 없음·중지: `LOCAL_MODEL_UNAVAILABLE`, 생성 0건
- 선택한 Model이 실행 전 변경·무효화됨: `MODEL_SELECTION_STALE`, 자동 대체·생성 0건
- 선택한 Daon 지식 Version·Digest·등록 상태가 무효: `KNOWLEDGE_CONTEXT_STALE`, Model 호출·생성 0건
- Raw Source 품질 Gate 미충족: 사용은 가능하되 `unverified_input` 경고와 강화된 review condition을 고정하며 Daon 지식으로 표시 0건
- Local Key 잠김·철회: `LOCAL_KEY_UNAVAILABLE`, 읽기·쓰기·재개 0건
- 설정 미확정: `SETTINGS_NOT_CONFIRMED`, RunSnapshot·OutputVersion 0건
- Evidence 없는 Section: 생성은 가능하지만 `unverified` 경고 고정
- Offline 상태: Queue는 `draft | awaiting_approval`, Cloud 전송·최종 승인 0건
- 승인 만료·철회·권한 축소: `SYNC_APPROVAL_REQUIRED` 또는 현재 권한 Safe Error, 전송 0건
- Version 충돌: `SYNC_VERSION_CONFLICT`, 자동 병합·덮어쓰기 0건
- 장치 Revoke·Sync Key 폐기: Queue 접근·재개 0건

## 4. 구성요소와 책임

### 4.1 Local Offline Studio Domain

Local Service에 하나의 조정 서비스 경계를 둔다.

```text
OfflineStudioService
  confirm_settings(...)
  generate_draft(...)
  append_edit(...)
  get_draft(...)
  queue_sync_preview(...)
```

이 서비스는 선행 Domain 계약을 재사용한다.

- `GenerationRequest`로 설정 확인·잠금
- `KnowledgeContextProjector`로 Daon Knowledge Package와 Raw Source를 origin·quality·authority가 분리된 불변 Context로 고정
- `LocalConversation` 또는 동등한 Local Retrieval Port로 Context 안의 Local-private Evidence 선택
- `ModelCatalogPort`로 Provider 설정·Ollama 설치 digest·completion capability·Policy 조건을 통과한 모델 목록 제공
- `DraftGenerationPort`로 사용자가 선택한 Ollama/Groq/Upstage Provider 생성
- `DocumentDraft`로 Section·Evidence·검토 상태 정규화
- Local Canon Repository로 불변 계보 저장

공통 `DraftGenerationPort`는 `KnowledgeContextSnapshot`과 `ModelSelectionSnapshot`을 함께 받는다. Adapter는 Provider code로 명시 선택되며 자동 Fallback을 허용하지 않는다. Offline 제품 Run은 Local Service의 `OLLAMA/server_internal`만 허용하고 Groq·Upstage 전송은 0건이어야 한다. Online 검증은 Cloud API 프로세스 안에서 외부전송이 승인된 정제 synthetic Knowledge Context와 `GROQ|UPSTAGE/external_api` Adapter 계약만 확인한다. Desktop에 Cloud credential을 전달하거나 Local-private Evidence를 전송하지 않는다. 선택 Model이 실행 시점에 unavailable·digest mismatch·policy invalid가 되면 다른 Provider나 Model로 바꾸지 않고 Provider별 safe error로 종료한다.

#### 4.1.1 Knowledge Context Snapshot

`KnowledgeContextSnapshot`은 다음을 고정한다.

- `context_mode`: `daon_priority | mixed | raw_only`
- Daon Knowledge Item: producer `daon2 | daon2_5 | daon3`, KnowledgeRegistration·OutputVersion ID/Version, Canon digest, authority, review/approval, effective/expiry time
- Evidence Resource Item: Source·SourceVersion·IndexVersion·EvidenceSpan ID/digest, media/structure-neutral locator, processing/review 상태, authority와 `unverified` 여부. Daon 생성 Text도 동일 Evidence Item이며 별도 LLM 제한을 두지 않는다.
- KnowledgeScope·WeightProfile Version, item별 retrieval weight와 선택 이유
- duplicate digest와 제외 이유, unresolved conflict와 acknowledgement
- Snapshot 생성 시각·schema version·canonical digest

`daon_priority`는 적격 Daon 지식이 없으면 조용히 Raw Source로 바꾸지 않고 `DAON_KNOWLEDGE_UNAVAILABLE`로 실패한다. `mixed`는 두 종류를 모두 사용하되 Citation에 origin과 quality를 표시한다. `raw_only`는 명시적으로 선택한 경우만 허용하며 결과에 `unverified_input`과 강화된 review condition을 고정한다.

#### 4.1.2 Model Selection Snapshot

`ModelSelectionSnapshot`은 provider code/kind, profile ID, deployment ID, model ID, model digest, binding version, generation settings, output schema digest, selection actor/time/policy version을 고정한다. Ollama는 `/api/tags`와 `/api/show`에서 확인한 exact name/digest/capabilities를 추가로 고정한다. UI는 opaque deployment ID만 전송하며 Endpoint·Path·임의 Model string을 지정하지 못한다. 동일 Run에서 Model을 바꾸지 않고 재생성은 새 GenerationRequest·RunSnapshot·OutputVersion을 만든다.

### 4.2 암호화 Local Canon

기존 `LocalEncryptedStore.canonical_envelopes`를 그대로 사용한다. 새 DB 파일이나 평문 JSON을 만들지 않는다. Canon entity allowlist에는 아래 세 종류만 추가한다.

- `GenerationRequest`
- `GenerationSettingsSnapshot`
- `ScopeSnapshot` — `KnowledgeContextSnapshot` 저장형

기존 허용 Entity를 함께 사용한다.

- `Run`, `RunSnapshot`
- `StudioOutput`, `OutputVersion`
- `PendingOperationReference`

저장 순서는 다음과 같다.

1. immutable `ScopeSnapshot`
2. confirmed `GenerationRequest`
3. immutable `GenerationSettingsSnapshot` — Model selection 포함
4. submitted `GenerationRequest` 새 Version
5. `Run`·`RunSnapshot`
6. 최초 `StudioOutput`·`OutputVersion`
7. 편집마다 `previous_version_id`가 직전 Version을 가리키는 새 `OutputVersion`
8. Sync 선택 시 `PendingOperationReference`

모든 payload는 `data_area=local_private`, Tenant에 종속되지 않는 Local Workspace UUID, schema version, canonical text, SHA-256 digest와 생성 시각을 가진다. 원문 Section body는 SQLCipher 경계 안에만 저장하며 Log·Audit·Evidence에는 digest와 opaque ID만 기록한다.

### 4.3 RunSnapshot 계약

Offline RunSnapshot은 최소 다음을 고정한다.

- Local Workspace ID
- GenerationRequest·SettingsSnapshot ID/Version
- KnowledgeContextSnapshot ID/digest/mode와 Daon Knowledge·Raw Source Item별 origin/Version/digest/quality
- Local SourceVersion·Citation/Evidence ID와 KnowledgeRegistration·OutputVersion 계보
- 선택된 Local Model provider/model/deployment/artifact와 각 Digest
- Template·review condition
- 네트워크 상태 `offline`
- egress `none`
- 생성 시각과 Trace ID

실행 중 Snapshot UPDATE를 금지한다. 재생성은 새 Run·RunSnapshot·OutputVersion을 만든다.

### 4.4 Offline Sync Queue

기존 `sync_queue_states`를 사용한다. Queue에는 Operation Reference, approval state, manifest digest, batch cursor, conflict reference만 저장하며 Content·Token·Key·Cloud URL은 저장하지 않는다.

오프라인 생성 직후에는 `draft`만 허용한다. Server Preview가 만들어지면 `awaiting_approval`, 사용자 승인 후 `approved`, 전송 중 `transferring`, 충돌 시 `conflict`, 완료 후 `reindex_requested`를 append한다. 기존 Version을 UPDATE·DELETE하지 않는다.

`list_resumable_sync_operations()` 결과가 있다고 자동 전송하지 않는다. Tauri 화면이 항목을 표시하고 사용자가 재개를 선택한 뒤에만 Native Cloud Client가 기존 Sync API를 호출한다.

### 4.5 Loopback Local API와 Tauri Bridge

초안 생성·편집을 위한 새 공개 API는 만들지 않는다. Local Service의 command-bound Loopback API에 Offline Studio 전용 내부 명령만 추가한다. Daon 지식을 오프라인 저장소로 Provision하는 공개 경계만 §4.5.1의 세 Path로 제한해 추가한다.

```text
POST /local/v1/studio/settings/confirm
POST /local/v1/studio/drafts/generate
GET  /local/v1/studio/drafts/{id}
POST /local/v1/studio/drafts/{id}/versions
POST /local/v1/studio/drafts/{id}/sync-queue
```

Capability는 read/write로 분리하고 command, method, exact path, 최대 body byte를 allowlist에 고정한다. Browser Origin·Proxy Header·query string·wildcard path·초과 body·재사용 nonce는 기존 middleware에서 거부한다.

React는 `fetch`, XHR, WebSocket을 사용하지 않고 Tauri invoke만 호출한다. Rust Bridge는 exact DTO allowlist, size cap, timeout, Content-Length, JSON response shape와 Safe Error를 검증한다. Local port, Token, storage root, key material과 내부 stack을 JS에 반환하지 않는다.

온라인 Knowledge Package를 실제 Windows 암호화 저장소로 반입하고 재연결 Sync 상태를 복구하려면 다음 네 개의 **Local Service 내부 명령**을 추가한다. 이들은 Cloud 공개 API나 Browser BFF가 아니며 Tauri Native Client만 command-bound Token으로 호출한다.

```text
POST /local/v1/studio/knowledge-copies
POST /local/v1/studio/knowledge-copies/{id}/refresh
GET  /local/v1/studio/sync-operations/{id}
POST /local/v1/studio/sync-operations/{id}/states
```

- 명령은 각각 `studio_knowledge_copy_import`, `studio_knowledge_copy_refresh`, `studio_sync_state_read`, `studio_sync_state_append`로 고정한다.
- Capability는 `knowledge.write`, `sync.read`, `sync.write`로 분리하고 다른 Studio Token으로 호출할 수 없게 한다.
- Knowledge import body는 Base64 포함 16MiB, refresh body는 32KiB, Sync state append body는 64KiB를 넘으면 domain write 전에 거부한다. Content-Length 부재·중복·Transfer-Encoding·query·unsafe ID도 거부한다.
- Import는 Package Manifest와 실제 decoded bytes를 다시 canonical parse하고 SHA-256을 대조한 뒤에만 AES-GCM File Store에 저장한다. SQLCipher에는 원문이 아니라 Object ID, Package/Producer/Registration/Output Version, authority/review/expiry, content digest와 불변 `ScopeSnapshot`만 저장한다.
- Cloud Package의 불변 Version identity는 별도 임의 정수가 아니라 `package_id + output_version_id + digest_sha256`다. `producer_version`은 Daon 생산 제품 버전이고 Local `ScopeSnapshot.version`은 Local append 순번이므로 서로 혼용하지 않는다.
- 동일 Package identity·Workspace의 exact idempotency replay만 동일 Copy를 반환한다. 다른 bytes·digest·OutputVersion·lineage로 같은 Key를 재사용하면 저장 0으로 거부한다.
- Refresh는 원문을 UPDATE하지 않는다. 승인 상태가 `approved | revoked | expired`로 바뀌면 같은 aggregate의 새 `ScopeSnapshot` Version을 append한다. 새 Package Version은 refresh가 아니라 새 import로 저장한다.
- Sync state 조회는 최신 append-only Version만 안전 DTO로 반환한다. State append는 기존 `sync_queue_states`의 previous version·manifest digest·cursor·conflict 계약을 그대로 사용하며 자동 전송을 시작하지 않는다.
- Browser Header, Proxy Header, wrong Host/capability/command/method/path, replay nonce, oversize, malformed Canon, digest mismatch, Cross Workspace에서는 File·Canon·Queue write가 모두 0이어야 한다.

#### 4.5.1 Daon Knowledge Package 오프라인 Provisioning

Daon 지식을 오프라인 주 입력으로 사용하려면 온라인 상태에서 승인된 Package를 Windows 암호화 저장소에 미리 Provision해야 한다. 기존 Local→Cloud Sync를 역방향으로 오용하지 않고 다음 공개 경계를 추가한다.

```text
GET  /api/v1/workspaces/{id}/knowledge-packages
POST /api/v1/workspaces/{id}/knowledge-packages/{package_id}/offline-copies
GET  /api/v1/offline-knowledge-copies/{copy_id}/content
```

- Package 목록은 registered KnowledgeRegistration과 approved OutputVersion만 투영한다.
- Offline copy 생성은 현재 Session·Device·Membership·Workspace ACL·Source/Output 접근·조직 정책과 `data_area_move` Step-up을 검사한다.
- 응답 Manifest는 producer product/version, KnowledgeRegistration·OutputVersion, Source/Evidence lineage, authority/review/effective/expiry, content type, bytes, canonical digest를 고정한다.
- Content는 Native Cloud Client만 받아 Local Service의 AES-GCM File Store와 SQLCipher Canon Envelope에 저장한다. Browser에는 bytes·Cloud URL·Local Path를 주지 않는다.
- Native Client는 Content를 받은 뒤 위 `studio_knowledge_copy_import` 명령으로만 Local Service에 전달한다. revoke·expiry 확인은 `studio_knowledge_copy_refresh`, 재연결 Queue 복구는 `studio_sync_state_read|append`만 사용한다. 일반 Storage 명령이나 Browser 경로로 우회하지 않는다.
- 동일 Package Version+digest+device는 idempotent copy ID로 수렴한다. Version/digest가 달라지면 새 copy이며 기존 copy를 UPDATE하지 않는다.
- 권한 축소·등록 취소·Device revoke·expiry는 이후 refresh에서 copy를 `revoked | expired`로 append하고 새 Run에서 사용 0건이다. 이미 생성된 RunSnapshot은 과거 재현 계보로만 남는다.

### 4.6 재연결 Cloud Sync

Cloud 동기화는 기존 R1-M5-05의 다섯 경로와 승인·재개·충돌 의미를 유지하되, Sync Item 계약을 SourceVersion과 OutputVersion을 구분하는 Versioned 계약으로 확장한다. 새 경로를 만들지 않는다.

- Sync Operation Preview 생성
- Operation 조회
- Step-up 승인
- Transfer Batch
- Conflict Resolution

Native Cloud Client는 승인된 public origin만 사용한다. Browser 코드는 Cloud URL을 알지 못한다. 전송 직전에 Server가 현재 Session·Device·Membership·Workspace ACL·Egress 정책·Step-up·Source/Output Version을 다시 검증한다.

Local-private 원본과 Local OutputVersion은 변경하지 않는다. Cloud에는 새 Version으로 Copy/Publish하고 Audit·Trace·Approval Snapshot과 연결한다. 실제 M6 재색인이 끝나기 전에는 `reindex_requested`만 표시한다.

#### 4.6.1 공개 Sync Item 계약

기존 Source Sync 요청을 깨지 않도록 `item_kind`의 기본값은 `source_version`으로 둔다. 공개 요청과 Domain은 다음 필드를 사용한다.

```text
item_kind: source_version | output_version = source_version
source_version_id: string | null
output_version_id: string | null
dependency_item_ids: string[] = []
```

검증 규칙은 다음과 같다.

- `source_version`: `source_version_id`만 필수이고 `output_version_id`와 `dependency_item_ids`는 비어 있어야 한다.
- `output_version`: `output_version_id`만 필수이고 `dependency_item_ids`에는 같은 Operation 안에서 먼저 전송해야 하는 `source_version` Item ID만 허용한다.
- 두 Version ID가 모두 있거나 모두 없으면 `SYNC_ITEM_INVALID`이다.
- 기존 Client가 `item_kind`를 생략하고 `source_version_id`를 보내는 요청은 이전과 동일한 Source Sync로 처리한다.
- Operation fingerprint, Preview digest, Approval Snapshot, Manifest digest, Conflict와 Audit에는 `item_kind`, 선택된 Version ID, 정렬된 dependency ID가 모두 포함된다.
- 승인 범위에 Output Item의 dependency가 빠져 있거나 dependency가 완료되기 전에 Output을 전송하면 `SYNC_DEPENDENCY_REQUIRED`로 fail-close하고 Output 전송·Canon write는 0건이다.

OutputVersion의 전송 payload는 일반 JSON이 아니라 `application/vnd.daon.offline-studio-output+json` Canon Bundle이다. Bundle은 Local OutputVersion, 직전 Version ID, KnowledgeContextSnapshot, ModelSelectionSnapshot, GenerationSettingsSnapshot, RunSnapshot, Section·Evidence Reference, Local SourceVersion dependency ID와 각 Canon digest를 포함한다. Daon Knowledge Item은 원 KnowledgeRegistration·OutputVersion·package digest를 참조하고 Raw Source Item은 Source dependency를 참조한다. Server는 실제 bytes의 SHA-256과 Manifest digest를 대조하고 exact key·schema version·Workspace·lineage·dependency 완료 상태를 검증한다. Browser나 Local Client가 Cloud Canon ID를 지정할 수 없다.

#### 4.6.2 Cloud Output import 경계

`ObjectQueueSyncTransferPort`는 `item_kind`로 전송을 분기한다.

- `source_version`: 기존 `area=source` Copy/Publish 동작을 그대로 유지한다.
- `output_version`: `area=output`으로 Object Queue에 제출하고, Server가 deterministic Cloud ID를 발급해 Cloud `GenerationSettingsSnapshot`, `GenerationRequest`, `StudioOutput`, `OutputVersion`을 만든다.

Cloud Canon은 Local ID를 Record ID로 재사용하지 않는다. 새 Cloud Canon payload에는 Local Workspace·Run·Settings·Output Version ID와 digest를 `offline_import_lineage`로 보존하고, 현재 Cloud Workspace 정책 Projection과 Import Actor·Trace·Approval Snapshot을 함께 고정한다. GenerationRequest는 `configuring → confirmed → submitted`, OutputVersion은 `generating → draft`의 기존 상태 전이를 통과한다. Local Evidence dependency는 해당 Source Item의 Sync Target과 digest로 보존하되 실제 Cloud SourceVersion·EvidenceSpan이 없는 상태에서 `EvidenceReference`를 거짓 생성하거나 `unverified`를 verified로 승격하지 않는다.

같은 Idempotency Key와 같은 Bundle digest는 동일 Cloud OutputVersion을 반환한다. ID나 digest가 다르면 `IDEMPOTENCY_KEY_REUSED`, Lineage·dependency·현재 권한·정책이 다르면 안전 오류로 거부한다. Object 제출 뒤 Canon transaction이 실패하면 성공 TargetVersion을 기록하지 않고 재시도는 동일 deterministic ID로 수렴한다.

`sync_target_versions.target_version_id`는 Output Item에서 실제 Cloud OutputVersion `record_id`를 가리킨다. Target record와 Audit는 `item_kind`를 보존한다. Source Item의 기존 Target 의미와 응답 필드는 바꾸지 않는다.

### 4.7 PostgreSQL Migration 0014와 배포 호환성

Migration `0014_offline_studio_sync`는 `offline_knowledge_copy_grants`를 추가하고 `sync_preview_items`, `sync_manifest_items`, `sync_target_versions`에 `item_kind`를 추가한다. Copy Grant는 Tenant·Workspace·Device·KnowledgeRegistration·OutputVersion·package digest·expiry·state·approval snapshot을 append-only로 저장하고 RLS·immutable·current access를 강제한다. Preview·Manifest에는 nullable `output_version_id`와 immutable `dependency_item_ids text[] NOT NULL DEFAULT '{}'`를 추가한다. 기존 `source_version_id`는 nullable로 바꾸되 아래 exact-one CHECK를 둔다.

```text
(item_kind='source_version' AND source_version_id IS NOT NULL AND output_version_id IS NULL)
OR
(item_kind='output_version' AND source_version_id IS NULL AND output_version_id IS NOT NULL)
```

기존 행은 `item_kind='source_version'`, `dependency_item_ids='{}'`로 deterministic backfill한다. Rolling deployment 중 구 API가 계속 INSERT할 수 있도록 DB default `source_version`을 유지한다. OutputVersion ID는 Local Canon ID이므로 Cloud `output_versions` FK로 거짓 결속하지 않는다. 대신 `sync_target_versions`에 nullable `target_output_version_id`를 추가하고, Output Item에서는 `target_output_version_id=target_version_id`를 강제한 뒤 Tenant·Workspace scoped Cloud `output_versions` FK로 결속한다. Source Item에서는 이 열이 null이며 기존 Target 의미를 유지한다. dependency 배열은 중복 없는 정렬된 Safe ID만 허용하는 Insert 검증 Trigger를 거쳐 Preview·Manifest와 Approval Snapshot digest에 동일하게 고정한다.

Upgrade는 backfill count, exact-one CHECK, digest·Approval Snapshot 결속, RLS와 기존 Source operation 재생을 실제 PostgreSQL에서 검증한다. Downgrade는 Output Item 또는 이를 참조하는 Target·Conflict·Batch가 하나라도 있으면 `SYNC_OUTPUT_DOWNGRADE_BLOCKED`로 fail-close한다. Output Item이 없을 때만 0013으로 되돌리고 기존 Source 행과 Operation 상태를 보존한다.

## 5. 데이터·보안 불변 조건

1. Local-private 원문과 초안은 승인 전 외부 전송 0건
2. External Provider 자동 Fallback 0건
3. 평문 SQLite·JSON Queue·임시 파일 0건
4. Browser의 API 절대주소·localhost·127.0.0.1·Docker 주소·`NEXT_PUBLIC_*` 직접 호출 0건
5. Local API는 loopback·instance-bound·short-lived command token만 허용
6. Key·Token·Password·원문·Local Path·Cloud 내부 URL Log/Evidence 노출 0건
7. Cross Workspace read/write/search/sync 0건
8. Version 충돌 자동 병합·덮어쓰기 0건
9. 승인 철회·만료·권한 축소 후 전송 0건
10. 동일 Idempotency Key 재요청의 중복 Version·전송 0건

## 6. 변경 범위

예상 제품 변경 범위는 다음으로 제한한다.

- Local Service Offline Studio Domain·DTO·command registry와 Knowledge Copy/Sync State 내부 명령 4종
- `LocalEncryptedStore` Canon allowlist와 Offline Studio 저장 Adapter
- Tauri Local Studio Bridge와 command 등록
- Desktop Offline Studio Adapter·상태 모델·Pane
- Desktop Workspace 3열 구조를 보존하는 NotebookLM-inspired Violet 시각 Token·Panel·App Bar
- 운영상태·설정 Modal Popup과 접근성·미저장 변경 Guard
- 기존 Sync API를 호출하는 Native 재연결 Adapter 연결
- 기존 다섯 Sync 경로의 Item DTO·Domain·PostgreSQL Adapter·OpenAPI 확장
- Daon Knowledge Package 목록·승인 Offline Copy·Native Content 세 공개 Path와 Grant 저장소
- Migration `0014_offline_studio_sync`와 actual PostgreSQL upgrade·rollback Gate
- Output Queue 제출과 Cloud Studio Canon import Adapter
- Unit·Local Integration·Rust Contract·Actual React·Groq/Upstage actual generation·Ollama installed-model connection Gate

Web BFF, Web Workspace Studio, Egress 정책값, 인증 모델과 공개 Sync 경로 수는 변경하지 않는다. 공개 Sync Item DTO와 PostgreSQL Schema는 위 Versioned 호환 계약 안에서만 확장한다. Object Storage는 기존 `source | output` 영역과 Object Queue만 재사용하며 새 Bucket·Key 규칙·외부 Provider를 만들지 않는다.

## 7. 검증 계약

### 7.1 TDD·자동 검증

- 설정 미확정·Local Model 부재·Local Key 잠김의 write 0 RED
- Daon Knowledge Package 등록·승인·Version/Digest·revoke/expiry와 Cross Workspace read 0
- `daon_priority | mixed | raw_only` Context의 origin·weight·Citation·warning 불변성
- 적격 Local Model 목록·선택 Snapshot·stale selection·자동 Fallback 0
- Offline 생성·편집 후 Canon 계보와 digest·previous version
- Restart 후 SettingsSnapshot·RunSnapshot·Draft·Queue 복구
- SQLCipher DB/WAL/SHM/File/Log 전체 고유 Canary 평문 0
- Network socket 차단 중 생성·편집 성공, 외부 연결 시도 0
- 다른 Workspace UUID의 Draft·Queue 조회 0
- Loopback capability/method/path/body/nonce 부정 Matrix
- Knowledge Copy import/refresh와 Sync state read/append의 wrong command·digest·oversize·Cross Workspace write 0
- React 실제 click으로 설정 확인→생성→편집→Queue 표시
- React 실제 DOM에서 3열 순서·Panel 의미·가운데 Draft Editor 전환·오른쪽 단계 View 유지
- 운영상태·설정 Popup open/close·Focus Trap·Escape·Focus 복귀·Background inert·미저장 Guard
- Light·Dark·1920×1080·1280×720에서 겹침·잘림 0, 기준 Font와 대비 유지
- 상시 설명 Box·브라우저 기본 Button 스타일·내부 기술 오류 노출 0
- React 제품 코드 fetch/XHR/WebSocket·내부 주소 0
- Reconnect 전송은 승인 없음·만료·권한 축소·Version 충돌에서 transport 0
- 승인된 exact 항목의 Batch 재개와 중복 전송 0
- 기존 Source-only JSON 요청의 응답·재개·충돌 동작 무변경
- Output Item exact-one Version ID·dependency 승인/순서·Bundle digest 부정 Matrix
- Output 전송 성공 시 Cloud GenerationSettingsSnapshot·StudioOutput·OutputVersion·TargetVersion exact lineage
- Migration 0014 기존 Source backfill·RLS·CHECK·upgrade/reapply와 Output 존재 downgrade fail-close
- 기존 Local Storage·Sync·Generation Settings·Document Draft·Desktop 전체 회귀

### 7.2 실제 Windows Gate

실제 설치형 Windows App에서 다음을 수행한다.

1. Online 상태에서 Daon2·2.5·3 Knowledge Package 목록과 Version/Authority 확인
2. Step-up 승인 후 선택 Package를 암호화 Local copy로 Provision
3. Local-private Workspace와 Raw Source 준비
4. `daon_priority | mixed | raw_only` Context와 origin/Citation/warning 확인
5. 적격 Local Model 2개 이상에서 한 Model 선택
6. 네트워크 차단
7. 초안 설정·확정·생성·Model 변경 재생성·편집
8. 앱·Local Service 재시작 후 동일 Version과 Model/Context Snapshot 복구
9. Process/Network에서 외부 Connection·DNS 0 확인
10. SQLCipher·암호문·Log 평문 Canary 0 확인
11. 연결 복구
12. Knowledge revoke/expiry refresh 후 새 Run 사용 0 확인
13. Sync 대기함 Preview 확인
14. Step-up 승인 전 전송 0 확인
15. 승인 후 exact 항목만 Batch 전송
16. 충돌 Fixture에서 자동 덮어쓰기 0과 명시 선택 확인

화면은 종료 시 닫고 Process·Listener·Temporary credential·Fixture를 정리한다. 실제 Windows Gate가 없으면 코드 자동계약은 완료할 수 있어도 `R1-WIN-01 PASS`나 M8 Exit로 승격하지 않는다.

## 8. 완료 조건

- 오프라인 초안 생성·편집·Restart 복구
- Daon Knowledge 기본·우선과 Raw Source 선택 입력이 origin·quality를 잃지 않고 함께 동작
- 사용자가 선택한 Local LLM과 실제 RunSnapshot Model digest 일치, 자동 대체 0
- GenerationSettingsSnapshot·RunSnapshot·StudioOutput·OutputVersion 계보 일치
- 암호화 Queue와 승인 전 외부 전송 0
- 재연결 승인 항목만 Sync, Source dependency 선행, Cloud OutputVersion 계보 일치, 충돌 자동 덮어쓰기 0
- 실제 설치 App·Network·암호화 증거
- 실제 설치 App에서 Violet 시각 체계·3열 구조·운영상태/설정 Popup·Keyboard Focus 검증
- 관련 자동 회귀·Desktop Build·Boundary·보안 Scan PASS
- 변경 파일·Evidence·Rollback·잔여 Process 0 보고

## 9. 제외 범위

- Web Offline 모드
- 모바일 Offline Studio
- 새 Cloud Sync 경로·새 Object Storage 영역
- 최종 승인·외부 Delivery·KnowledgeRegistration의 Offline 성공 처리
- Cloud 재색인 완료 위장
- 새로운 암호화 알고리즘·Key 저장소·별도 Local DB
- 자동 충돌 병합·자동 재개 전송

## 10. 2026-08-14 Web 최종 화면 승인 개정

신산님은 현재 ysna-server의 Web Workspace를 승인된 `NotebookLM-inspired Violet` 최종 화면으로 교체해 `daon-user.sinsan.kr`에서 직접 검토하는 것을 승인했다. 따라서 §6의 `Web Workspace Studio를 변경하지 않는다`는 백엔드·BFF·공개 API·데이터 계약을 변경하지 않는다는 의미로 한정하며, Web 사용자 화면의 시각 Shell·상태 표현·상호작용은 이 개정 범위에서 변경한다.

- 기존 Source·Conversation·Studio 3면의 의미, 실제 callback, same-origin BFF와 API DTO는 유지한다.
- 기본 Workspace, 생성 유형 선택, 보고서 설정, 생성 중·실패·완료, 저장 산출물 상세, 설정 Menu와 `LLM 설정` Popup을 실제 제품 화면으로 구현한다.
- `LLM 설정`은 9 Provider의 실제 설정 상태를 표시하되, 연결되지 않은 Action은 가짜 성공 없이 `미설정 | 연결 필요 | 준비 중`으로 fail-close한다.
- 현재 업무 기능을 시각적으로 재배치하되 내부 정책 코드, SQLSTATE, Stack, 내부 URL과 Credential 원문을 노출하지 않는다.
- 화면 승인용 실제 React 구현과 ysna Web-only 배포는 허용한다. DB Migration, API·Worker·공용 서비스 변경은 허용하지 않는다.
- 완료 Gate는 1920×1080 실제 브라우저의 기본·보고서 설정·LLM 설정·생성/완료 상태, same-origin Network, 기존 Source·질문·Studio 기능 보존이다.

## 11. 2026-08-14 개발 순서 확정 — 공통 기반 후 메뉴별 완성

화면 승인 이후의 기능 개발은 아래 원칙을 적용한다.

1. 여러 메뉴가 공유하는 모듈과 API를 먼저 개발·검증한다. 이미 구현된 계약은 중복 작성하지 않고 현재 동작·회귀·actual Gate를 확인한 뒤 부족한 부분만 보완한다.
2. 공통 기반 완료 전에는 개별 메뉴의 기능 구현을 시작하지 않는다. 단, 공통 기반을 검증하기 위한 최소 Test Harness는 제품 메뉴 구현으로 간주하지 않는다.
3. 이후 메뉴 하나를 선택해 Domain·DB/Repository·API/BFF·화면·운영 경고·actual Browser까지 완성한다.
4. 현재 메뉴에 Critical·Important 미해결이 있거나 실제 Gate가 열려 있으면 다음 메뉴로 이동하지 않는다.
5. 승인된 App Bar·3면 Workspace·Studio Tile·Library·Popup 시각 구조는 유지하고 기능 연결 때문에 화면 전체를 다시 설계하지 않는다.

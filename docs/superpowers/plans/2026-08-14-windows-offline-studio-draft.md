# Windows Offline Studio Draft Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 선택된 Notebook의 NotebookLM-inspired 3열 작업 화면을 로그인과 분리된 검증 Harness에서 먼저 완성하고, 공통 모듈·API와 공통 설정을 닫은 뒤 Notebook 홈을 구현하며, 마지막에 로그인→Notebook 홈→선택 Notebook 3열 화면을 결합한다.

**Architecture:** Phase A에서 인증·권한, Canon·계보, Provider·Model, 입력·근거, 생성·산출물, same-origin BFF 상태 계약을 공통 기반으로 검증·보완한다. Phase B에서는 선택된 Notebook의 3열 화면 메뉴를 수직 완성한다. Phase C에서 Theme·License·Manual 공통 기능을 구현하고, Phase D에서 Notebook Domain/API와 홈을 구현한다. Phase E에서 홈→3열을 결합한 뒤 로그인 연결을 가장 마지막에 추가한다.

**Tech Stack:** Python 3.14/FastAPI/SQLCipher/AES-GCM/psycopg, Rust/Tauri 2, React/JavaScript, PostgreSQL 15·18, JSON Schema/OpenAPI, Node test runner, pytest, Cargo test.

## Global Constraints

- 승인 설계 정본은 `docs/superpowers/specs/2026-08-14-windows-offline-studio-draft-design.md`이며 초기 시각 설계 commit은 `4665840f61451b284bec0b46209eb7d01f6daf84`다. 이번 이중 입력·모델 선택·Knowledge Provisioning 개정본을 승인 Commit한 뒤 그 SHA를 Work Order에 고정한다.
- 공식 작업공간은 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, 통합 기준선은 `origin/master`다. 현재 계획 작성 Branch는 `codex/user-auth-screen-split`이며 실행 전 승인 문서가 `master`에 반영됐거나 신산님이 별도 실행 Branch를 지정했는지 확인한다. 불일치하면 코드를 수정하지 않고 보고한다.
- 한 시점에 한 Writer만 허용하며 어울1은 설계·검토를 소유하고 구현은 `daon-developer` 어울2에게 전체 문서로 전달한다.
- 현재 Windows Workspace의 `Source · 대화/실행 · 업무 Studio` 3열 위치·폭·의미를 유지한다. Dashboard나 별도 Cockpit으로 재배치하지 않는다.
- `Workspace`는 조직·권한·정책 경계이고 `Notebook`은 Source·대화·산출물을 묶는 사용자 작업 단위다. 하나의 Workspace를 하나의 Notebook으로 간주하지 않는다.
- 3열 개발 Harness는 운영 인증 우회가 아니다. 운영 API의 Session·ACL·RLS·CSRF·Step-up은 유지하고 Harness는 운영 Bundle·공개 Route 밖에서 명시 Test Notebook Context와 Adapter만 주입한다.
- 로그인 화면과 3열 화면의 제품 연결은 Phase E까지 제거한다. 운영 비인증 Route와 보호 API의 401 계약은 그대로 유지한다.
- 화면 설정은 `system | light | dark`, OS Theme 변경 감지, 화면 Preference 전용 초기화를 제공하며 Notebook 데이터를 변경하지 않는다.
- 라이선스는 서명 검증·조직 범위·만료·한도·사용량·잔여·경고를 서버 권위로 관리한다. Private key·License 원문·내부 판정 정보는 Browser/Bundle/Git에 저장하지 않는다.
- 사용자 설명서는 실제 완료 기능만 설명하는 Markdown 정본에서 Getting Started·사용자 설명서·지식/LLM 활용 가이드를 Release별로 생성하고 Web 읽기·DOCX·PDF를 제공한다.
- 화면 기준은 1920×1080, 제목 16px, Panel 제목 14px, 본문·Form 12px, 보조 10px, 아주 작은 보조 9px이다.
- 시각 언어는 `NotebookLM-inspired Violet`: 중립 Canvas, 불투명 Surface, 얇은 Border, 13px Radius, 절제된 Shadow, Violet 단일 Accent를 선택·주요 Action·Citation·활성 상태에만 사용한다.
- 긴 설명 Box를 상시 노출하지 않는다. 필수 Label은 화면에 두고 추가 설명은 `i` Icon Tooltip·Popover로 제공한다.
- 운영상태와 설정은 App Bar Button으로 여는 별도 Modal Popup이다. Critical 상태는 App Bar 상태와 관련 Panel Inline Alert에도 남겨 Popup에만 숨기지 않는다.
- 새 Windows Workspace 화면과 이번 범위에서 수정하는 화면은 동일 App Bar·Surface·Button·Inline Alert·Modal·Focus 계약을 재사용한다. 무관한 기존 화면을 일괄 재설계하지 않는다.
- Browser/React 코드의 `fetch`, XHR, WebSocket, API 절대주소, `localhost`, `127.0.0.1`, Docker Host·Port, `NEXT_PUBLIC_*` 직접 호출은 0건이어야 한다.
- Local Service Loopback만 `127.0.0.1`을 사용할 수 있으며 Rust가 Port·Token·Path·Timeout·응답 크기를 소유한다. JS에는 Local Port·Token·Storage Root·Key·Stack을 반환하지 않는다.
- Knowledge Package 반입과 Sync 복구는 Tauri 전용 Local Service 내부 명령 4종만 사용한다. Capability는 `knowledge.write | sync.read | sync.write`로 분리하고 Browser·일반 Studio Token·일반 Storage 명령 우회를 금지한다.
- Local-private 원문·초안·Evidence는 승인 전 외부 전송 0건, External·server_internal Provider 자동 Fallback 0건이다.
- 입력 계약은 `daon_priority | mixed | raw_only` 세 mode다. 이 mode는 권위·선택 정책을 나타낼 뿐 LLM이 사용할 수 있는 지식 형식을 제한하지 않는다. Daon2·2.5·3의 등록 지식이 기본·우선이며 모든 형식의 사용자·외부 Source 사용도 유지한다.
- Daon2·2.5·3 내부 DB·Module·Path 직접 의존은 금지한다. 표준 Knowledge Package, Connector와 명시 KnowledgeRegistration만 사용한다.
- Raw Source를 Daon 지식으로 자동 승격하지 않는다. Raw-only 결과에는 `unverified_input`과 강화된 review condition을 고정한다.
- 모든 Citation은 `daon_knowledge | raw_source` origin, producer/version, authority/quality, 원 Version digest와 형식 중립 opaque locator를 보존한다. origin은 계보 표시용이며 Retrieval·생성 능력 분기값이 아니다.
- Model은 Provider 설정의 eligible deployment 목록에서 사용자가 선택한다. Offline 목록에는 `OLLAMA/server_internal` 중 `/api/tags` exact digest와 `/api/show`의 `completion` capability가 확인된 로컬 모델만 포함하며 `:cloud`, remote-host, embedding-only 모델을 제외한다. 선택 Provider·Model을 자동 교체하지 않는다.
- Local 원문은 기존 SQLCipher DB와 AES-GCM File Store 안에만 저장한다. 평문 SQLite·JSON Queue·임시 Content 파일을 추가하지 않는다.
- Daon은 Ollama 모델을 설치·삭제하거나 임의 실행파일을 Secure Store에 등록하지 않는다. 기존 Ollama Provider 설정과 설치 모델 Catalog를 재사용하고 Local Service 내부 `OllamaDraftGenerationAdapter`를 추가한다. 별도 executable subprocess는 제품 실행 경계에서 제거하고 fixture test 전용으로만 격리한다. Ollama Service·선택 모델·exact digest/capability를 확인할 수 없으면 `LOCAL_MODEL_UNAVAILABLE`로 fail-close한다.
- 실제 생성 기능·품질 Gate는 `UPSTAGE | GROQ | MISTRAL` 중 대표 하나를 명시 선택해 수행한다. 호환성 의심 시에만 두 번째 Provider를 추가하고 전체 Provider 반복 기능 시험은 금지한다. 나머지는 설정·연결·모델 조회·Health까지만 확인한다.
- 기존 다섯 Sync 공개 Path는 유지한다. `item_kind` 생략 Source Client는 이전과 동일하게 동작해야 한다.
- Output Sync는 `application/vnd.daon.offline-studio-output+json` Canon Bundle만 허용하고 Source dependency 완료 전 전송·Cloud Canon write는 0건이다.
- Migration `0014_offline_studio_sync`는 PostgreSQL 15와 18에서 Knowledge copy grant, fresh upgrade, 0013→0014, rollback/reapply, RLS, legacy Source replay, Output downgrade fail-close를 실제 DB로 검증한다.
- 오류·상태·Evidence에는 내부 URL, Local Port, Token, Password, Key, 원문, Local Path, Stack, SQLSTATE를 포함하지 않는다.
- 기존 Web Workspace의 DOM·same-origin BFF·질문·Studio 기능은 Desktop 옵션이 없을 때 변경되지 않아야 한다.
- 각 Task는 RED 확인 → 최소 GREEN → 관련 회귀 → `git diff --check` → 허용 파일만 좁은 Commit 순서로 종료한다. Commit·Push·배포는 신산님의 별도 승인 경계다.

---

## 신산님 승인 Foundation-first·Menu-by-menu 실행 순서

최종 화면 Shell은 `R1-M8-10-WEB-FINAL-UI-I001`에서 구현·배포된 상태를 기준선으로 고정한다. 기존 Task 1~8의 기술 산출물은 보존하지만, 남은 구현과 재작업 순서는 아래가 우선한다.

### Phase A — 공통 모듈·공통 API 우선

1. **A1 인증·운영 안전 공통 계약**: Session, Workspace ACL, Step-up, CSRF, Idempotency, safe error, Audit를 Web·Desktop·API 공통 경계로 정리하고 actual deny/write0를 검증한다.
   - `0015_security_audit_step_up_idempotency`는 기존 Cloud `audit_events`와 분리한 Security Audit persistence다. production composition은 in-memory store를 사용하지 않는다. Step-up same-key replay/conflict/concurrency와 sensitive domain replay-before-consume는 Identity 저장소의 schema-versioned 단일 원장에서 발급·소비 상태와 원자적으로 검증하며, 사용되지 않는 PostgreSQL 이중 원장은 두지 않는다.
   - `DAON_STEP_UP_TOKEN_KEY_FILE`은 server-only root-owned file reference다. replay token은 key-versioned domain-separated HMAC으로 재구성하며 raw/ciphertext를 Identity 원장·API·log에 저장하지 않는다. production key absent 및 rotation pending-replay는 fail-close다.
2. **A2 Canon·저장·계보 공통 계약**: immutable Canon, Version/Binding, ETag, RLS, deterministic ID, RunSnapshot, OutputVersion과 rollback/replay를 공통 Repository/API로 검증한다.
   - OutputVersion의 내용 계보 Version과 상태 전이 낙관적 잠금 Version을 같은 칼럼에 혼용하지 않는다. `0016_output_version_content_lineage`에서 `content_version`을 분리하고 최신 선택·previous chain·동시 same-key replay를 실제 PostgreSQL로 검증한다. 다중 내용 Version이 존재하면 구 Schema downgrade는 SQLSTATE 55000으로 fail-close한다.
3. **A3 Provider·Model 공통 계약**: 9 Provider의 Profile·Credential reference·Endpoint safe projection·Deployment·ModelSelectionSnapshot·connection check·health 상태를 동일 계약으로 완성한다. 기능 품질 시험은 `UPSTAGE | GROQ | MISTRAL` 중 대표 하나, 필요 시 두 번째만 사용한다.
4. **A4 입력·근거 공통 계약**: PDF·문서·Text·Markdown·웹·표·이미지·음성·영상·DB/API Projection·Daon2·2.5·3 Knowledge Snapshot을 형식 독립적 `EvidenceResource`로 수용한다. 수집 Adapter만 원본을 bounded Evidence Item으로 정규화하고 LLM Retrieval/Prompt는 출처 종류로 기능을 제한하지 않는다. Context Snapshot·Evidence·Citation·권위·최신성·충돌·opaque locator를 공통 입력 계약으로 고정한다.
5. **A5 생성·산출물 공통 계약**: Generation Settings, Routing/Egress Decision, Provider transport, Citation validator, Output 저장·Version·검토·승인·Export를 메뉴들이 재사용하는 생성 Pipeline으로 완성한다.
6. **A6 Web 공통 연결 계약**: same-origin BFF, Loading/Empty/Ready/Warning/Error 상태, 재시도, 접근성 Modal, 운영상태 경고를 공통 UI adapter와 Projection으로 검증한다.

Phase A의 각 항목은 기존 구현을 먼저 대조한다. 이미 닫힌 계약은 재작성하지 않고 회귀 증거만 갱신하며, 누락된 계약만 RED→GREEN으로 보완한다. A1~A6에 Critical·Important가 없고 전체 API/DB/BFF Actual Gate가 통과해야 Phase B로 이동한다.

### Phase B — 화면 메뉴를 하나씩 수직 완성

각 메뉴는 `Domain → Repository/DB → API → same-origin BFF → React UI → 1920×1080 actual Browser → 독립 검토`를 하나의 작업 단위로 수행한다. 현재 메뉴가 완료되기 전 다음 메뉴를 동시에 개발하지 않는다.

1. **설정 → LLM 설정**: 9 Provider 설정, Credential 설정 여부, Endpoint·Deployment·Model, 연결 시험, 기본 모델과 역할 매핑.
2. **Source·지식·권위**: Knowledge Snapshot·Raw Source 등록/선택, 처리 상태, 권위·Version·충돌·오류 복구.
3. **대화·실행**: 선택 Source/Knowledge Context, 질문, 추가 인증, 대표 LLM 답변, Citation, 실패·재시도·Run 보존.

   기존 Question Endpoint를 호환 확장한다. legacy `source_id/source_version_id` 또는 새 `knowledge_context` 중 정확히 하나만 허용한다. 새 Context DTO는 `mode`, 선택 Resource와 Knowledge Package ID를 받고, 서버는 각 입력을 공통 `EvidenceResource → EvidenceItem`으로 정규화한 뒤 전체 Item을 하나의 불변 Snapshot/Run/추가 인증 fingerprint로 결속한다. LLM payload는 형식이나 origin으로 기능을 제한하지 않는다. 응답 Citation은 `origin`, `context_item_id`, resource/version 계보와 opaque locator를 포함하고 Web은 선택 Context 밖 Citation을 거부한다. Citation Renderer는 질문/추론과 분리하며 지원 형식만 표시하고 미지원 Renderer는 안전 오류로 처리한다. 이 Domain·PostgreSQL persistence·OpenAPI·BFF·React·actual Browser Gate가 닫히기 전 Studio 메뉴로 이동하지 않는다.
4. **Studio → 근거 기반 보고서**: 생성 설정, 실제 생성, 저장, 상세, Version, Citation, 검토·승인·Export.
5. **Studio → 제약·준수 점검표**.
6. **Studio → 비교·데이터 표**.
7. **Studio → 지식 구조도**.
8. **Studio → 업무 문서 초안**.
9. **Library → 저장 산출물 통합 관리**: 유형별 목록, 상세, Version, 편집, 재생성, 검토·승인·내보내기.
10. **운영상태**: Provider/API/Storage/Sync/Queue 상태, 안전 경고와 복구 Action.
11. **설정 → 출력·버전**.
12. **설정 → 동기화·승인**.
13. **설정 → 조직 정책**.

`슬라이드 | 인포그래픽 | 플래시카드 | 퀴즈 | AI 오디오 | 동영상`은 위 메뉴가 닫힌 뒤 별도 설계 승인으로 순서를 추가한다. 준비 중 Tile은 그 전까지 disabled 상태를 유지한다.

### Phase C — Notebook 홈과 공유하는 공통 설정

Phase B 13개 메뉴가 완료된 현재 기준선에서 다음 세 메뉴를 순서대로 수직 완성한다.

1. **설정 → 화면 설정**
   - 사용자 Preference Domain과 `system | light | dark` exact DTO를 먼저 고정한다.
   - 권위 저장은 `0018 user_screen_preferences`의 tenant·actor scope와 FORCE RLS로 한정하며, Notebook 데이터 table/write path를 재사용하거나 추가하지 않는다.
   - Web same-origin Preference API와 Windows encrypted local projection을 분리하고 자동 Client 동기화는 하지 않는다.
   - OS Theme 변경, 초기 paint, Light/Dark token, reduced-motion, 화면 설정만 초기화, Notebook 데이터 write0을 검증한다.
   - 1920×1080 Light/Dark와 200% Zoom actual Browser/Desktop Gate를 수행한다.
2. **설정 → 라이선스**
   - License document Schema·signature·organization scope·issue/expiry·feature/resource limits를 Domain/API로 구현한다.
   - 일반 사용자는 read-only, 조직 관리자만 Step-up 후 apply 가능하다.
   - 상태·사용량·잔여·30일 경고·만료·한도 차단을 화면과 API에서 동일하게 표시한다.
   - Private key 저장0, invalid signature·wrong product/org·expired·replay·cross-tenant write0, Audit/RLS actual PostgreSQL을 검증한다.
3. **설정 → 사용자 설명서**
   - 메뉴만 구현하지 않고 실제 설명서 제작을 같은 작업 범위에서 완료한다.
   - 한국어 Markdown 정본과 Release Version manifest를 먼저 만든다.
   - `Daon Getting Started`, `Daon 사용자 설명서`, `Daon 지식·LLM 활용 가이드`의 실제 본문을 작성한다.
   - 각 문서에 현재 제품의 실제 1920×1080 화면 캡처, 클릭 위치, 입력 조건, 예상 결과, 권한, 실패 시 조치를 포함한다.
   - Markdown에서 DOCX·PDF를 생성하고 페이지 잘림·표·이미지·목차·링크를 render 검사한다.
   - Menu Hub에서 문서 검색·Web 읽기·DOCX/PDF Download·Release Version 확인을 제공한다.
   - 현재 화면명·권한·Safe error와 문서 내용이 일치하는지 자동 Link/Heading/Version 검사와 실제 Browser Gate로 확인한다.
   - 산출물은 최소 `docs/manual/daon-getting-started`, `docs/manual/daon-user-manual`, `docs/manual/daon-knowledge-llm-guide`, `docs/manual/dist`와 Web download asset manifest를 포함한다.

Reference baseline은 NowNote `c3fdef73ef66dba2e7ff63f372cbd316fb5eb639`과 CGA `6d04bc9e4eb0942c8ecb5e8f9816d4cbd6720153`다. 구현을 복사하지 않고 Daon 계약에 맞게 재설계한다.

### Phase D — Notebook Domain·API·홈

1. `Notebook`, `NotebookBinding`, `NotebookActivity`의 Tenant/Workspace scope, immutable create record, current metadata Version, RLS·Audit·Idempotency 계약을 설계한다.
2. Notebook create/list/get/update-title API와 same-origin BFF를 구현한다. 삭제·공유·추천·Template은 이번 범위에서 제외한다.
3. Notebook 홈에 새 Notebook, 최근·기존 Notebook, 검색, 최근 수정/제목 정렬, Grid/List 전환, Loading/Empty/Error를 구현한다.
4. Notebook Card는 title, Source count, Output count, updated_at, safe status만 표시한다.
5. 새 Notebook 생성→빈 3열 화면 이동, 기존 Notebook→보존된 Source/대화/Library 재진입을 actual PostgreSQL과 Browser로 검증한다.

### Phase E — 제품 조립과 로그인 최종 연결

1. `GET /api/v1/workspaces/{workspace_id}/notebooks/{notebook_id}/context`와 exact same-origin BFF를 추가하고, Source·Question·Studio read/write DTO에 canonical `notebook_id`를 필수로 결속한다. Context는 selected IDs와 현재 Conversation Thread의 safe answer/citations projection을 반환하며 별도 Conversation Route는 만들지 않는다. Question 성공 transaction은 Thread·answer/citations·Binding·Run/Audit을 원자 생성하고 replay duplicate0을 보장한다. 서버는 Tenant/Workspace/Notebook Binding membership을 검증하고 생성 Resource를 같은 Notebook에 원자 귀속한다. missing·invalid·mismatch와 cross-tenant/workspace/notebook은 fail-close하며 Workspace ID만으로 Notebook을 자동 선택하지 않는다.
2. `Notebook 홈 → 선택 Notebook 3열 화면`을 먼저 연결하고 뒤로 가기·새로고침·직접 URL·Session 내 Workspace 전환을 검증한다.
3. 독립 3열 Harness가 운영 Build·Route·Bundle에 포함되지 않음을 Boundary 검사로 고정한다.
4. 마지막에 로그인 성공 Redirect를 `/notebooks` 홈으로 변경한다. 로그인 응답이 Workspace ID를 반환해도 임의 Notebook을 자동 선택하지 않는다.
5. 비인증→로그인, 인증→Notebook 홈, Notebook 선택→3열, Session 만료→안전한 로그인 복귀를 actual Browser에서 검증한다.
6. same-origin Network, Cookie/Token 비노출, ACL/RLS cross-workspace·cross-notebook read/write0, 로그아웃 후 Browser history 재진입 차단을 확인한다.
7. Review1 Gate에서 Windows Native 7개 Source·Question·Studio transport의 exact Notebook scope와 rich Citation, Source/processing SQL-level Binding prefilter, Web exact projection을 검증한다.
8. self logout outbox는 startup bounded recovery와 immutable DB guard를 갖추고, BFCache 보호 root는 `pagehide`에서 동기 은폐한 뒤 server Session 재검증 후에만 공개한다.
9. disposable actual PostgreSQL DB/Role에서 exact non-skip test ID와 cleanup db0/role0을 secret-free Evidence로 결속한다.
10. Windows production `main.jsx → DesktopShell`은 로그인 뒤 서버 권위 Notebook Home을 표시하고, 명시적으로 선택·재검증된 Notebook만 3열 Native Adapter에 전달한다. create/existing select/back/refresh/direct deep-link/history/workspace switch/expiry/logout을 실제 state machine과 Native wire로 검증하며 default·first·fixed Notebook 선택은 0이다.

Phase E 통합 Gate 전까지 개발 검증은 로그인 조작을 요구하지 않는 독립 Harness를 사용한다. 하지만 해당 Harness PASS를 운영 인증 통합 PASS로 대체하지 않는다.

아래 Task 0~8 상세 절은 이미 구현된 자산과 파일·테스트 계약의 참조 기록이다. 남은 작업의 실제 착수 순서와 Go/No-Go는 위 Phase A·B가 우선하며, Task 번호 순서대로 다시 구현하지 않는다.

### Task 0: 최종 Workspace·Studio·설정 화면

**Files:**
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `apps/desktop/src/desktop-shell.css`
- Modify: `apps/desktop/src/offline-studio-pane.jsx`
- Modify: `apps/desktop/src/workspace-settings-modal.jsx`
- Modify: `apps/desktop/src/workspace-operations-modal.jsx`
- Modify: `apps/desktop/src/workspace-visual-tokens.css`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `packages/ui/src/workspace.css`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`
- Modify: `scripts/tests/offline-studio-ui.test.mjs`
- Modify: `scripts/tests/windows-workspace-visual.test.mjs`
- Modify: `scripts/tests/windows-workspace-modal.test.mjs`

**Interfaces:**
- Consumes: 기존 Workspace·Source·Question·Studio·Provider 상태 객체와 현재 Action callback. 새 API·DB·Network 호출을 만들지 않는다.
- Produces: 최종 App Bar, 설정 Dropdown, `LLM 설정` View, 3열 Workspace, Studio 유형 Grid, 통합 산출물 Library와 명시적 unavailable 상태.

- [ ] **Step 1: 실제 React 화면 RED를 작성한다**

1920×1080에서 App Bar와 Source·Conversation·Studio 3열이 viewport 안에 있고 horizontal scroll이 없어야 한다. 기존 3열의 위치·의미는 유지하되 NotebookLM과 같은 조밀한 작업 Surface와 시각 위계를 사용한다. 긴 상시 설명 Box, 전역 하단 오류 Box, Browser 기본 Button/Input, 파일명 Bullet 반복, 내부 정책 코드가 DOM에 있으면 실패한다. 설정 Button 클릭 후 anchored Dropdown에 `LLM 설정`이 보이고, 클릭하면 Focus-trap Modal 안에 Provider 카드와 실제 상태가 표시되어야 한다.

- [ ] **Step 2: Studio 생성 Grid·산출물 Library RED를 작성한다**

Studio 상단에는 NotebookLM처럼 3열 Tile Grid를 둔다. 현재 제공하는 보고서·점검표·데이터 표·지식 구조도·업무 문서 초안은 활성 유형으로 표시한다. 슬라이드·인포그래픽·플래시카드·퀴즈·AI 오디오·동영상은 후속 Phase Label과 disabled `준비 중` 상태로 표시하며 클릭 성공을 가장하지 않는다. Tile은 Icon·Label·Chevron·muted tint를 가지며 선택 Tile만 Violet Accent로 강조한다. 저장 산출물은 유형 Icon·제목·사용 Source 수·Version·생성시각·상태·More Menu를 한 목록에 표시하고 실제 재생 가능 유형만 Play Action을 노출한다.

- [ ] **Step 3: 최종 시각 Shell을 최소 구현한다**

NotebookLM의 정보 위계와 밀도를 참고하되 Google 상표·문구·Asset을 복제하지 않는다. Neutral Canvas, opaque Surface, 13px Radius, 얇은 Border, 절제된 Shadow, Violet Accent, 16/14/12/10/9px Typography를 적용한다. App Bar는 한 줄, Panel Header는 Icon+Title+상태/Action, Source는 조밀한 List Row, Conversation은 중앙 Transcript+하단 고정 Composer, Studio는 Tile Grid+Library로 구성한다. 기본 HTML Control처럼 보이지 않도록 height·padding·focus·hover·disabled를 통일하고, 빈 공간은 Empty State·최근 항목·작업 상태로 사용한다.

- [ ] **Step 4: 설정 Dropdown과 LLM 설정 View를 구현한다**

상단 `설정`은 NotebookLM처럼 Button 아래 anchored Dropdown을 열고 `LLM 설정`, `출력·버전`, `동기화·승인`, `조직 정책` 항목을 Icon과 함께 표시한다. `LLM 설정` View는 9개 Provider를 Card Grid로 표시하고 선택 Card의 연결 상태·Credential 설정 여부·Endpoint·Model 상세만 오른쪽 또는 하단 Detail Pane에 보여준다. 저장·연결 Action은 기존 callback이 없으면 disabled `연결 기능 준비 중` 상태이며 가짜 성공을 만들지 않는다.

- [ ] **Step 5: 접근성·반응형·Theme를 구현한다**

Modal `role=dialog`, 제목 결속, 최초 Focus, Tab/Shift+Tab trap, Escape, background inert, opener focus return을 구현한다. 1366×768과 200% Zoom에서도 조작 가능하고 768px 이하는 Source→Conversation→Studio 순서를 유지한다. Light/Dark와 reduced-motion을 지원한다.

- [ ] **Step 6: GREEN과 실제 화면 Gate를 실행한다**

```powershell
node --test scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/offline-studio-ui.test.mjs scripts/tests/windows-workspace-visual.test.mjs scripts/tests/windows-workspace-modal.test.mjs scripts/tests/product-workspace.test.mjs
node scripts/verify-product-ui-boundary.mjs --target desktop
Set-Location apps/desktop
npm run build
```

실제 Desktop WebView에서 1920×1080 Light/Dark, 설정→LLM 설정, keyboard focus, 내부 URL·Token·Stack 노출 0을 Screenshot과 DOM으로 확인한다. 이 Gate 전에는 Task 0을 완료로 판정하지 않는다.

신산님이 제공한 NotebookLM 참고 화면과 나란히 비교해 다음을 수동 확인한다: App Bar 밀도, 설정 Menu의 위치·크기, Studio 3열 Tile Grid, Library Row의 정보 위계, Panel의 불필요한 빈 공간, Browser 기본 Form 잔존 여부. 기능이 같더라도 이 시각 Gate를 통과하지 못하면 `INCOMPLETE`다.

---

### Task 1: Daon Knowledge Offline Provisioning, Sync Item Versioned Contract와 Migration 0014

**Files:**
- Create: `services/api/migrations/versions/0014_offline_studio_sync.py`
- Create: `services/api/src/daon_user_api/knowledge_package.py`
- Create: `services/api/src/daon_user_api/knowledge_package_postgres.py`
- Modify: `services/api/src/daon_user_api/sync.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Modify: `services/api/src/daon_user_api/sync_postgres.py`
- Modify: `packages/contracts/openapi/v1/openapi.json`
- Create: `services/api/tests/test_sync_output_migration_contract.py`
- Create: `services/api/tests/test_knowledge_package.py`
- Create: `services/api/tests/test_knowledge_package_runtime_http.py`
- Create: `services/api/tests/test_knowledge_package_postgres.py`
- Modify: `services/api/tests/test_sync_domain.py`
- Modify: `services/api/tests/test_sync_contract.py`
- Modify: `services/api/tests/test_sync_runtime_http.py`
- Modify: `services/api/tests/test_sync_postgres.py`
- Modify: `scripts/tests/openapi-contract.test.mjs`
- Modify: `scripts/verify-openapi-contract.mjs`

**Interfaces:**
- Consumes: registered `KnowledgeRegistration`, approved `OutputVersion`, current access/Step-up/device contracts, 기존 `SyncContext`, `SyncService`, 다섯 Sync HTTP Path와 Sync tables.
- Produces: `KnowledgePackageView`, `OfflineKnowledgeCopyGrant`, `KnowledgePackageService`, 세 Knowledge Package public paths, `SyncItemKind`, 확장 Sync DTO, Migration `revision="0014"`.

- [ ] **Step 1: Daon Knowledge Package 조회·승인 copy RED를 작성한다**

```python
def test_only_registered_approved_daon_knowledge_can_be_provisioned_offline():
    packages = service.list_packages(context)
    assert packages == (KnowledgePackageView(
        package_id="knowledge-package-1", producer="daon3",
        knowledge_registration_id="registration-1",
        output_version_id="output-version-1", authority="approved",
        digest_sha256="a" * 64, byte_size=4096,
    ),)
    grant = service.create_offline_copy(
        context, package_id="knowledge-package-1", device_id="device-1",
        step_up_authorization_id="step-up-1", idempotency_key="knowledge-copy-0001",
    )
    assert grant.state == "approved"
```

unregistered/rejected output, stale access, wrong device, missing `data_area_move` Step-up, expired package, digest mismatch, idempotency reuse를 각각 package bytes 0·grant success 0으로 고정한다.

- [ ] **Step 2: legacy Source와 Output exact-one 계약의 RED를 작성한다**

```python
def test_output_item_requires_exact_version_and_sorted_source_dependencies():
    item = SyncItemInput(
        item_id="item-output-1", source_version_id=None,
        local_object_id="object-output-1", digest_sha256="a" * 64,
        byte_size=12, content_type="application/vnd.daon.offline-studio-output+json",
        base_cloud_version_id=None, base_cloud_digest=None,
        item_kind=SyncItemKind.OUTPUT_VERSION,
        output_version_id="local-output-version-1",
        dependency_item_ids=("item-source-1",),
    )
    assert item.version_id == "local-output-version-1"

def test_legacy_source_item_omitting_kind_is_unchanged():
    item = SyncItemInput(
        "item-source-1", "source-version-1", "object-source-1", "b" * 64,
        12, "application/pdf", None, None,
    )
    assert item.item_kind is SyncItemKind.SOURCE_VERSION
```

Migration 정적 테스트는 `item_kind`, nullable `source_version_id`, `output_version_id`, `dependency_item_ids`, `target_output_version_id`, exact-one CHECK, dependency Trigger, backfill, RLS와 downgrade guard가 없어서 실패해야 한다.

- [ ] **Step 3: RED를 실행해 Knowledge Package와 Source-only 계약 부재를 확인한다**

Run:

```powershell
Set-Location services/api
$env:PYTHONPATH='src;tests'
uv run --isolated --with pytest==9.0.3 --with argon2-cffi --with httpx --with "psycopg[binary]==3.3.4" --with psycopg-pool --with fastapi --with minio python -m pytest tests/test_knowledge_package.py tests/test_knowledge_package_runtime_http.py tests/test_knowledge_package_postgres.py tests/test_sync_domain.py tests/test_sync_contract.py tests/test_sync_runtime_http.py tests/test_sync_output_migration_contract.py -q
Remove-Item Env:PYTHONPATH
```

Expected: Knowledge Package service/path, `SyncItemKind` 또는 `0014_offline_studio_sync.py` 부재로 FAIL. Runner import 오류는 기능 RED로 계산하지 않는다.

- [ ] **Step 4: Knowledge Package Domain·Runtime을 구현한다**

```python
@dataclass(frozen=True, slots=True)
class KnowledgePackageView:
    package_id: str
    producer: str
    knowledge_registration_id: str
    output_version_id: str
    authority: str
    digest_sha256: str
    byte_size: int

class KnowledgePackageService:
    def list_packages(self, context: KnowledgePackageContext) -> tuple[KnowledgePackageView, ...]:
        raise NotImplementedError
    def create_offline_copy(self, context: KnowledgePackageContext, *, package_id: str, device_id: str, step_up_authorization_id: str, idempotency_key: str) -> OfflineKnowledgeCopyGrant:
        raise NotImplementedError
    def read_content(self, context: KnowledgePackageContext, *, copy_id: str) -> bytes:
        raise NotImplementedError
```

Runtime은 `GET /workspaces/{id}/knowledge-packages`, `POST /workspaces/{id}/knowledge-packages/{package_id}/offline-copies`, `GET /offline-knowledge-copies/{copy_id}/content`를 exact DTO/headers로 연결한다. Content는 Native client 전용 device/session binding과 8MiB cap을 적용한다.

- [ ] **Step 5: Sync Domain과 Runtime DTO를 하위 호환 확장한다**

```python
class SyncItemKind(str, Enum):
    SOURCE_VERSION = "source_version"
    OUTPUT_VERSION = "output_version"

@dataclass(frozen=True, slots=True)
class SyncItemInput:
    item_id: str
    source_version_id: str | None
    local_object_id: str
    digest_sha256: str
    byte_size: int
    content_type: str
    base_cloud_version_id: str | None
    base_cloud_digest: str | None
    item_kind: SyncItemKind = SyncItemKind.SOURCE_VERSION
    output_version_id: str | None = None
    dependency_item_ids: tuple[str, ...] = ()

    @property
    def version_id(self) -> str:
        return self.source_version_id if self.item_kind is SyncItemKind.SOURCE_VERSION else self.output_version_id  # type: ignore[return-value]
```

`__post_init__`은 Source exact-one·empty dependencies, Output exact-one·전용 media type·정렬/중복 없는 Safe ID dependencies를 검증하고 위반 시 `SYNC_ITEM_INVALID`을 반환한다. `SyncItemBody`도 같은 기본값과 nullable 필드를 사용한다.

- [ ] **Step 6: Migration 0014를 구현한다**

```python
revision = "0014"
down_revision = "0013"

def upgrade() -> None:
    op.execute("""
        CREATE TABLE offline_knowledge_copy_grants (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          copy_id text NOT NULL,
          package_id text NOT NULL,
          device_id text NOT NULL,
          actor_id text NOT NULL,
          knowledge_registration_id text NOT NULL,
          output_version_id text NOT NULL,
          producer text NOT NULL CHECK (producer IN ('daon2','daon2_5','daon3')),
          package_digest text NOT NULL CHECK (package_digest ~ '^[0-9a-f]{64}$'),
          byte_size bigint NOT NULL CHECK (byte_size > 0 AND byte_size <= 8388608),
          step_up_authorization_digest text NOT NULL CHECK (step_up_authorization_digest ~ '^[0-9a-f]{64}$'),
          state text NOT NULL CHECK (state IN ('approved','revoked','expired')),
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          approved_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id,workspace_id,copy_id),
          UNIQUE (tenant_id,workspace_id,device_id,package_id,package_digest),
          UNIQUE (tenant_id,workspace_id,actor_id,idempotency_key),
          FOREIGN KEY (tenant_id,workspace_id) REFERENCES workspaces(tenant_id,workspace_id),
          FOREIGN KEY (tenant_id,workspace_id,knowledge_registration_id)
            REFERENCES knowledge_registrations(tenant_id,workspace_id,record_id),
          FOREIGN KEY (tenant_id,workspace_id,output_version_id)
            REFERENCES output_versions(tenant_id,workspace_id,record_id)
        )
    """)
    op.execute("ALTER TABLE sync_preview_items ADD COLUMN item_kind text NOT NULL DEFAULT 'source_version'")
    op.execute("ALTER TABLE sync_preview_items ALTER COLUMN source_version_id DROP NOT NULL")
    op.execute("ALTER TABLE sync_preview_items ADD COLUMN output_version_id text")
    op.execute("ALTER TABLE sync_preview_items ADD COLUMN dependency_item_ids text[] NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE sync_manifest_items ADD COLUMN item_kind text NOT NULL DEFAULT 'source_version'")
    op.execute("ALTER TABLE sync_manifest_items ALTER COLUMN source_version_id DROP NOT NULL")
    op.execute("ALTER TABLE sync_manifest_items ADD COLUMN output_version_id text")
    op.execute("ALTER TABLE sync_manifest_items ADD COLUMN dependency_item_ids text[] NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE sync_target_versions ADD COLUMN item_kind text NOT NULL DEFAULT 'source_version'")
    op.execute("ALTER TABLE sync_target_versions ADD COLUMN target_output_version_id text")
```

실제 Grant table은 Tenant·Workspace·Device·KnowledgeRegistration·OutputVersion 결속, Step-up authorization digest, canonical digest, state `approved|revoked|expired`, immutable/RLS/current access를 둔다. 세 Sync Table 모두 Tenant·Workspace scoped FK와 exact-one CHECK를 둔다. dependency Trigger는 `output_version`에서만 Safe ID·정렬·중복 제거 상태를 허용한다. Downgrade는 live Grant 또는 Output Item·Target·Conflict·Batch 참조가 있으면 `OFFLINE_STUDIO_DOWNGRADE_BLOCKED` SQLSTATE `55000`으로 전체 transaction을 rollback한다.

- [ ] **Step 7: PostgreSQL 저장·hydrate·fingerprint를 새 필드까지 연결한다**

`_dump_operation`, `_hydrate`, Preview/Manifest/Target INSERT·SELECT, `_fingerprint`가 `item_kind`, 선택 Version ID, 정렬 dependency IDs를 보존하게 한다. Source-only 행의 JSON/응답 필드는 이전과 같고 새 필드는 기본값으로만 추가한다.

- [ ] **Step 8: OpenAPI를 exact schema로 갱신한다**

```json
{
  "item_kind": {"type": "string", "enum": ["source_version", "output_version"], "default": "source_version"},
  "source_version_id": {"type": ["string", "null"]},
  "output_version_id": {"type": ["string", "null"]},
  "dependency_item_ids": {"type": "array", "items": {"$ref": "#/components/schemas/CanonId"}, "default": []}
}
```

OpenAPI에는 세 Knowledge Package path와 exact request/response/content media schema도 추가한다. Runtime behavior test는 package list/copy/content, legacy body 생략 Source 201, invalid exact-one 400 `SYNC_ITEM_INVALID`, missing Output dependency 409 `SYNC_DEPENDENCY_REQUIRED`를 단언한다.

- [ ] **Step 9: focused GREEN과 실제 PostgreSQL 15/18 Gate를 실행한다**

Run the Step 2 command plus:

```powershell
node scripts/verify-openapi-contract.mjs
node --test scripts/tests/openapi-contract.test.mjs
```

Actual Gate: disposable DB에서 `0001→0014`, `0013→0014`, registered+approved package grant/content, revoked/expired read 0, cross-tenant 0, legacy Source request replay, live Grant/Output row downgrade fail-close, references 제거 뒤 `0014→0013→0014`, prefix DB remaining 0을 확인한다.

- [ ] **Step 10: 허용 파일만 Commit한다**

```powershell
git add services/api/migrations/versions/0014_offline_studio_sync.py services/api/src/daon_user_api/knowledge_package.py services/api/src/daon_user_api/knowledge_package_postgres.py services/api/src/daon_user_api/sync.py services/api/src/daon_user_api/runtime.py services/api/src/daon_user_api/sync_postgres.py packages/contracts/openapi/v1/openapi.json services/api/tests/test_knowledge_package.py services/api/tests/test_knowledge_package_runtime_http.py services/api/tests/test_knowledge_package_postgres.py services/api/tests/test_sync_output_migration_contract.py services/api/tests/test_sync_domain.py services/api/tests/test_sync_contract.py services/api/tests/test_sync_runtime_http.py services/api/tests/test_sync_postgres.py scripts/tests/openapi-contract.test.mjs
git commit -m "feat: provision Daon knowledge for offline Studio"
```

---

### Task 2: Cloud Offline Studio Output Bundle Import

**Files:**
- Create: `services/api/src/daon_user_api/offline_studio_import.py`
- Modify: `services/api/src/daon_user_api/sync_postgres.py`
- Create: `services/api/tests/test_offline_studio_import.py`
- Modify: `services/api/tests/test_sync_postgres.py`
- Create: `services/api/tests/test_offline_studio_import_postgres.py`

**Interfaces:**
- Consumes: Task 1 `SyncItemInput.item_kind`, `dependency_item_ids`, `TargetVersion`, Object Queue `area="output"`, Cloud Canon tables.
- Produces: `OfflineStudioOutputBundle`, `parse_offline_studio_output_bundle(content: bytes, expected_digest: str)`, `PostgresOfflineStudioImportService.import_bundle(context, item, content, idempotency_key, relation)`, `ObjectQueueSyncTransferPort(coordinator, output_importer=importer)`.

- [ ] **Step 1: Canon Bundle와 dependency failure RED를 작성한다**

```python
def test_output_import_requires_completed_source_dependency_and_exact_bundle():
    with pytest.raises(SyncError) as denied:
        service.import_bundle(context, output_item, bundle_bytes, "idem-output-0001", relation="copy")
    assert denied.value.code == "SYNC_DEPENDENCY_REQUIRED"
    assert repository.output_version_count == 0
```

Malformed bytes, digest mismatch, extra key, Workspace mismatch, false verified Evidence, idempotency key reused, Canon transaction rollback도 각각 write 0을 단언한다.

- [ ] **Step 2: RED를 실행한다**

Run:

```powershell
Set-Location services/api
$env:PYTHONPATH='src;tests'
uv run --isolated --with pytest==9.0.3 --with "psycopg[binary]==3.3.4" --with psycopg-pool --with minio python -m pytest tests/test_offline_studio_import.py tests/test_sync_postgres.py -q
Remove-Item Env:PYTHONPATH
```

Expected: `offline_studio_import` module 부재로 FAIL.

- [ ] **Step 3: Bundle parser를 exact key·digest 계약으로 구현한다**

```python
@dataclass(frozen=True, slots=True)
class OfflineStudioOutputBundle:
    schema_version: int
    local_workspace_id: str
    knowledge_context_snapshot: dict[str, object]
    model_selection_snapshot: dict[str, object]
    generation_settings_snapshot: dict[str, object]
    run_snapshot: dict[str, object]
    studio_output: dict[str, object]
    output_version: dict[str, object]
    source_dependencies: tuple[dict[str, str], ...]

def parse_offline_studio_output_bundle(content: bytes, expected_digest: str) -> OfflineStudioOutputBundle:
    if len(content) > 8 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected_digest:
        raise SyncError("SYNC_CONTENT_DIGEST_MISMATCH", 400)
    document = json.loads(content)
    if set(document) != {"schema_version", "local_workspace_id", "knowledge_context_snapshot", "model_selection_snapshot", "generation_settings_snapshot", "run_snapshot", "studio_output", "output_version", "source_dependencies"}:
        raise SyncError("SYNC_OUTPUT_BUNDLE_INVALID", 400)
    return OfflineStudioOutputBundle(
        schema_version=document["schema_version"],
        local_workspace_id=document["local_workspace_id"],
        knowledge_context_snapshot=document["knowledge_context_snapshot"],
        model_selection_snapshot=document["model_selection_snapshot"],
        generation_settings_snapshot=document["generation_settings_snapshot"],
        run_snapshot=document["run_snapshot"],
        studio_output=document["studio_output"],
        output_version=document["output_version"],
        source_dependencies=tuple(document["source_dependencies"]),
    )
```

Parser는 canonical JSON bytes 재직렬화가 입력 bytes와 같아야 하며 모든 nested Canon digest를 재계산한다. Knowledge Context의 Daon Item은 원 KnowledgeRegistration/OutputVersion/package digest, Raw Item은 Source dependency/digest를 exact 검증하며 origin을 바꾸지 못한다.

- [ ] **Step 4: Cloud Import를 하나의 transaction으로 구현한다**

`PostgresOfflineStudioImportService.import_bundle`은 현재 Session/Workspace 정책과 승인 Snapshot을 다시 읽고, Task 1 dependency target이 모두 완료된 SourceVersion인지 검증한다. deterministic Cloud ID는 `(tenant, workspace, item_id, bundle_digest)` SHA-256으로 서버가 발급한다. GenerationRequest는 `configuring→confirmed→submitted`, OutputVersion은 `generating→draft` transition을 통과한다. `offline_import_lineage`에는 Local ID와 digest만 저장하며 Local ID를 Cloud record_id로 쓰지 않는다.

- [ ] **Step 5: Object Queue transfer를 item kind로 분기한다**

```python
if item.item_kind is SyncItemKind.SOURCE_VERSION:
    return self._transmit_source(context, item, content, idempotency_key, relation=relation)
return self._output_importer.import_bundle(
    context, item, content, idempotency_key, relation=relation,
)
```

Source branch의 기존 `area="source"` behavior는 byte-for-byte 기존 테스트로 고정한다. Output branch만 `area="output"`을 사용한다.

- [ ] **Step 6: idempotency·rollback·unverified behavior를 GREEN으로 만든다**

같은 key+digest는 같은 `target_output_version_id`, 다른 digest는 `IDEMPOTENCY_KEY_REUSED`, Canon failure는 target success 0, dependency Evidence가 Cloud EvidenceSpan으로 검증되지 않으면 `unverified` 유지임을 테스트한다.

- [ ] **Step 7: 실제 PostgreSQL Gate와 회귀를 실행한다**

Actual Gate는 Source dependency → Output Bundle 순서로 전송해 Cloud 네 Canon row, exact lineage, target FK, Audit를 확인한다. Output을 먼저 보내면 ObjectQueue/Canon/Audit success 0이어야 한다. DB cleanup remaining 0을 기록한다.

- [ ] **Step 8: Commit한다**

```powershell
git add services/api/src/daon_user_api/offline_studio_import.py services/api/src/daon_user_api/sync_postgres.py services/api/tests/test_offline_studio_import.py services/api/tests/test_sync_postgres.py services/api/tests/test_offline_studio_import_postgres.py
git commit -m "feat: import approved offline Studio outputs"
```

---

### Task 3: Dual Knowledge Context, 공통 Provider 선택과 Offline Studio Domain

**Files:**
- Modify: `services/local-service/src/daon_user_local_service/managed_local_draft.py`
- Create: `services/local-service/src/daon_user_local_service/provider_draft.py`
- Create: `services/local-service/src/daon_user_local_service/knowledge_context.py`
- Create: `services/local-service/src/daon_user_local_service/offline_studio.py`
- Modify: `services/local-service/src/daon_user_local_service/local_storage.py`
- Create: `services/local-service/tests/fixtures/managed_model_fixture.py`
- Create: `services/local-service/tests/test_provider_draft.py`
- Create: `services/local-service/tests/test_managed_local_draft.py`
- Create: `services/local-service/tests/test_knowledge_context.py`
- Create: `services/local-service/tests/test_offline_studio.py`
- Modify: `services/local-service/tests/test_local_storage.py`

**Interfaces:**
- Consumes: `LocalEncryptedStore.append_canonical_envelope`, `append_sync_queue_state`, Daon Knowledge Package/KnowledgeRegistration projection, Raw SourceVersion·IndexVersion·EvidenceSpan projection, Provider profile/deployment/binding projection, Ollama `/api/tags|show|chat`, Groq·Upstage server-side credentials.
- Produces: `KnowledgeContextMode`, `KnowledgeContextItem`, `KnowledgeContextSnapshot`, `KnowledgeContextProjector`, `ProviderModelDescriptor`, `ModelCatalogPort`, `ModelSelectionSnapshot`, `DraftGenerationPort`, `OllamaDraftGenerationAdapter`, `GroqDraftGenerationAdapter`, `UpstageDraftGenerationAdapter`, `OfflineStudioService`, `OfflineStudioError`, `ConfirmedSettingsView`, `OfflineDraftView`.

- [ ] **Step 1: 실제 wire·no-fallback·Canon 순서 RED를 작성한다**

```python
def test_generate_draft_uses_daon_priority_context_and_selected_local_model(store, fixture_model, context_projector):
    service = OfflineStudioService(
        store=store, context_projector=context_projector,
        model_catalog=MODEL_CATALOG, generator=fixture_model, clock=clock,
    )
    context = service.prepare_context(
        workspace_id=WORKSPACE, mode="daon_priority",
        daon_knowledge_ids=("knowledge-daon3-1",), raw_source_version_ids=("source-v1",),
    )
    confirmed = service.confirm_settings(
        workspace_id=WORKSPACE, request=SETTINGS,
        context_snapshot_id=context.snapshot_id,
        model_deployment_id="deployment-local-2",
    )
    draft = service.generate_draft(workspace_id=WORKSPACE, request_id=confirmed.request_id)
    assert draft.output_version == 1
    assert draft.egress == "none"
    assert draft.context.mode == "daon_priority"
    assert draft.context.items[0].origin == "daon_knowledge"
    assert draft.model_selection.deployment_id == "deployment-local-2"
    assert store.list_canonical_types(WORKSPACE) == (
        "ScopeSnapshot", "GenerationRequest", "GenerationSettingsSnapshot", "GenerationRequest",
        "Run", "RunSnapshot", "StudioOutput", "OutputVersion",
    )
    assert fixture_model.external_calls == 0
```

Daon priority인데 등록 지식 없음, stale KnowledgeRegistration/digest, raw→Daon 자동승격, mixed origin 손실, raw-only warning 누락을 각각 RED로 고정한다. Ollama 미설치 모델, `:cloud`, embedding-only, stale selected deployment, digest mismatch, timeout, output cap 초과, settings 미확정, non-local Source, key lock, restart edit version, same idempotency replay도 각각 fail-close로 고정한다. Offline Run에서 Groq·Upstage transport가 0인지 확인한다.

- [ ] **Step 2: RED를 실행한다**

Run:

```powershell
Set-Location services/local-service
$env:PYTHONPATH='src;tests'
uv run --isolated --with pytest==9.0.3 --with fastapi --with httpx --with cryptography --with pydantic python -m pytest tests/test_knowledge_context.py tests/test_managed_local_draft.py tests/test_offline_studio.py tests/test_local_storage.py -q
Remove-Item Env:PYTHONPATH
```

Expected: 세 module/contract 부재와 Canon allowlist 거부로 FAIL.

- [ ] **Step 3: 이중 입력 Knowledge Context를 구현한다**

```python
class KnowledgeContextMode(str, Enum):
    DAON_PRIORITY = "daon_priority"
    MIXED = "mixed"
    RAW_ONLY = "raw_only"

@dataclass(frozen=True, slots=True)
class KnowledgeContextItem:
    item_id: str
    origin: str
    producer: str
    version_id: str
    digest: str
    authority: str
    quality_state: str
    weight: float

@dataclass(frozen=True, slots=True)
class KnowledgeContextSnapshot:
    snapshot_id: str
    workspace_id: str
    mode: KnowledgeContextMode
    items: tuple[KnowledgeContextItem, ...]
    knowledge_scope_id: str
    weight_profile_id: str
    warnings: tuple[str, ...]
    digest: str
```

Projector는 Daon 지식의 Producer `daon2|daon2_5|daon3`, KnowledgeRegistration·OutputVersion·review/approval·effective/expiry·digest를 검증한다. Raw Source는 SourceVersion·IndexVersion·EvidenceSpan·processing/review/authority를 보존한다. `daon_priority`는 적격 Daon 지식 0건이면 `DAON_KNOWLEDGE_UNAVAILABLE`; `mixed`는 origin별 Citation을 유지; `raw_only`는 명시 선택만 허용하고 `unverified_input`과 강화 review condition을 넣는다. 동일 digest 중복은 제외 이유를 Snapshot에 남기며 상충 Raw 근거를 조용히 숨기지 않는다.

- [ ] **Step 4: 선택 가능한 공통 Provider Model Catalog와 immutable selection을 구현한다**

```python
@dataclass(frozen=True, slots=True)
class ModelSelectionSnapshot:
    provider_code: str
    provider_kind: str
    profile_id: str
    deployment_id: str
    model_id: str
    model_digest: str
    binding_version: int
    deployment_digest: str
    temperature: float
    max_output_tokens: int
    output_schema_digest: str

class ModelCatalogPort(Protocol):
    def list_eligible(self, *, workspace_id: str) -> tuple[ProviderModelDescriptor, ...]:
        raise NotImplementedError
    def select(self, *, workspace_id: str, deployment_id: str) -> ModelSelectionSnapshot:
        raise NotImplementedError
```

Catalog는 Provider 설정의 active/current binding과 policy를 통과한 deployment를 반환한다. Offline 목록은 `OLLAMA/server_internal`이면서 Ollama `/api/tags` exact digest 및 `/api/show`의 `completion` capability가 확인된 모델만 반환하고 `:cloud`, remote-host, embedding-only 모델을 제외한다. UI는 opaque deployment ID만 전송한다. confirm과 generate 사이 selection이 바뀌면 `MODEL_SELECTION_STALE`; 자동 fallback은 0이다.

- [ ] **Step 5: 공통 Provider 생성 포트와 Ollama/Groq/Upstage Adapter를 구현한다**

```python
@dataclass(frozen=True, slots=True)
class ProviderModelDescriptor:
    provider_code: str
    provider_kind: str
    profile_id: str
    deployment_id: str
    model_id: str
    model_digest: str
    deployment_digest: str

class DraftGenerationPort(Protocol):
    def generate(self, *, selection: ModelSelectionSnapshot, context: KnowledgeContextSnapshot, request: dict[str, object], timeout_seconds: float = 90.0) -> dict[str, object]:
        raise NotImplementedError
```

`OllamaDraftGenerationAdapter`는 내부 allowlisted base URL의 `/api/chat`만 사용하고 `stream=false`, JSON Schema, bounded timeout/body, exact selected model을 적용한다. `/api/tags`와 `/api/show`로 설치·digest·completion capability를 재검증한다. `:cloud`와 remote-host 모델은 거부하며 인증정보가 없는 로컬 호출만 허용한다. 제품 코드의 arbitrary executable/subprocess 실행 경계는 제거한다.

`GroqDraftGenerationAdapter`와 `UpstageDraftGenerationAdapter`는 Cloud API 프로세스의 서버측 Provider credential과 allowlisted HTTPS endpoint에서만 테스트·사용한다. Desktop/Local Service 모듈에 Cloud credential·external transport를 조립하지 않는다. 두 Adapter는 동일 system/user payload, output JSON Schema, Citation/grounding validator를 공유하되 Provider별 response_format 지원 차이를 명시적으로 처리한다. Groq actual Gate의 strict JSON Schema 모델은 공식 지원 모델을 선택한다. Provider 오류 시 다른 Provider로 fallback하지 않는다. 이번 Work Order는 새 Cloud Studio 생성 공개 API를 추가하지 않는다.

- [ ] **Step 6: Offline Studio Domain과 append-only Canon을 구현한다**

```python
class OfflineStudioService:
    def list_models(self, *, workspace_id: str) -> tuple[EligibleModelView, ...]:
        raise NotImplementedError
    def prepare_context(self, *, workspace_id: str, mode: str, daon_knowledge_ids: tuple[str, ...], raw_source_version_ids: tuple[str, ...], idempotency_key: str) -> KnowledgeContextSnapshot:
        raise NotImplementedError
    def confirm_settings(self, *, workspace_id: str, request: ConfirmSettingsInput, context_snapshot_id: str, model_deployment_id: str, idempotency_key: str) -> ConfirmedSettingsView:
        raise NotImplementedError
    def generate_draft(self, *, workspace_id: str, request_id: str, idempotency_key: str) -> OfflineDraftView:
        raise NotImplementedError
    def append_edit(self, *, workspace_id: str, draft_id: str, previous_version_id: str, sections: tuple[SectionInput, ...], idempotency_key: str) -> OfflineDraftView:
        raise NotImplementedError
    def get_draft(self, *, workspace_id: str, draft_id: str) -> OfflineDraftView:
        raise NotImplementedError
    def queue_sync_preview(self, *, workspace_id: str, draft_id: str, output_version_id: str, source_dependency_ids: tuple[str, ...], idempotency_key: str) -> SyncQueueDraftView:
        raise NotImplementedError
```

`RunSnapshot`에는 local workspace/request/settings, KnowledgeContextSnapshot ID/digest/mode와 모든 item origin/version/quality, ModelSelectionSnapshot 전체, template/review/offline/egress none/trace/time을 고정한다. Evidence 없는 Section은 생성하되 `unverified`를 지울 수 없다. 편집은 previous_version_id exact CAS 후 새 OutputVersion만 append한다.

- [ ] **Step 7: Local Canon allowlist와 Queue payload를 최소 확장한다**

`_CANON_ENTITY_TYPES`에 `GenerationRequest`, `GenerationSettingsSnapshot`, `ScopeSnapshot`만 추가한다. `ScopeSnapshot`은 Knowledge Context의 Daon/Raw origin·version·digest·quality를 exact canonical payload로 저장한다. Queue에는 IDs, digest, state, cursor, conflict만 저장하고 Section body·Token·Key·Cloud URL이 없음을 raw encrypted DB/Log scan으로 검증한다.

- [ ] **Step 8: fixture transport와 Provider GREEN을 실행한다**

Fixture는 stdin schema를 검증하고 deterministic Section JSON만 stdout에 쓰며 network module을 import하지 않는다. 제품은 fixture path를 참조하지 않는다.

- [ ] **Step 9: restart·key lock·process tree 실제 Gate를 실행한다**

Local Service를 재기동해 draft/version/queue가 복구되고 key lock 후 read/write/resume 0, timeout 후 descendant process 0, plaintext search 0을 확인한다.

- [ ] **Step 10: Commit한다**

```powershell
git add services/local-service/src/daon_user_local_service/knowledge_context.py services/local-service/src/daon_user_local_service/managed_local_draft.py services/local-service/src/daon_user_local_service/offline_studio.py services/local-service/src/daon_user_local_service/local_storage.py services/local-service/tests/fixtures/managed_model_fixture.py services/local-service/tests/test_knowledge_context.py services/local-service/tests/test_managed_local_draft.py services/local-service/tests/test_offline_studio.py services/local-service/tests/test_local_storage.py
git commit -m "feat: generate drafts from selected Daon and raw knowledge"
```

---

### Task 3B: Production Composition, Desktop Flow와 Immutable Provider Lineage 재작업

**Files:**
- Modify: `services/local-service/src/daon_user_local_service/main.py`
- Modify: `services/local-service/src/daon_user_local_service/app.py`
- Modify: `services/local-service/src/daon_user_local_service/offline_studio.py`
- Modify: `services/local-service/src/daon_user_local_service/provider_draft.py`
- Modify: `services/local-service/tests/test_app.py`
- Modify: `services/local-service/tests/test_offline_studio.py`
- Create: `services/local-service/tests/test_production_offline_studio.py`
- Modify: `apps/desktop/src/offline-studio-pane.jsx`
- Modify: `apps/desktop/src/offline-studio-model.js`
- Modify: `apps/desktop/src/offline-studio-adapter.js`
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `apps/desktop/src/workspace-operations-modal.jsx`
- Modify: `apps/desktop/src/workspace-settings-modal.jsx`
- Modify: `apps/desktop/src-tauri/src/offline_studio_bridge.rs`
- Modify: `apps/desktop/src-tauri/tests/offline_studio_bridge_contract.rs`
- Modify: `scripts/tests/offline-studio-ui.test.mjs`
- Modify: `scripts/tests/windows-workspace-modal.test.mjs`

**Interfaces:**
- Consumes: Task 3 `OfflineStudioService`, Ollama catalog/adapter, encrypted Local store, current Native Session workspace, existing Tauri command-bound token.
- Produces: actual `main.py` composition, workspace-bound model/context/draft calls, exact React→Rust→Local DTO flow, immutable selection lineage, truthful operations/settings projection.

- [ ] **Step 1: production 조립·UI DTO·flow RED를 작성한다**

실제 `main.py` app factory에서 `/local/v1/studio/models`가 `LOCAL_STUDIO_UNAVAILABLE`가 아닌 제품 Service를 사용해야 한다. React integration은 `listModels → prepareContext → confirmSettings → generateDraft → appendEdit → queueSync` 호출 순서와 exact Rust DTO keys를 검증한다. `workspace_id="local-profile"`, null context snapshot, generate 호출 0인 현재 상태에서 RED여야 한다.

- [ ] **Step 2: Local Service 제품 composition을 최소 구현한다**

`main.py`는 encrypted store, workspace-scoped Knowledge projector, Provider projection, Ollama catalog/adapter, `OfflineStudioService`를 조립해 `create_app(..., offline_studio=service)`로 전달한다. endpoint/base URL은 내부 설정에서만 읽고 UI·응답·로그로 반환하지 않는다. `/studio/models`는 request/session에 결속된 workspace를 사용하며 hard-coded workspace를 금지한다.

- [ ] **Step 3: Desktop full flow와 exact DTO를 연결한다**

Pane은 Source/Knowledge 선택으로 context를 먼저 준비하고 반환 snapshot ID를 사용해 settings를 확인한다. 확인 성공 후 generation action을 제공하고 draft/edit/version/queue actions를 실제 adapter로 연결한다. React request keys는 Rust DTO와 exact 일치하며 unknown/missing keys, stale revision, duplicate click은 domain write 0이다.

- [ ] **Step 4: immutable Provider lineage를 Settings·Run·Output에 고정한다**

`GenerationSettingsSnapshot`은 context snapshot ID/digest와 전체 `ModelSelectionSnapshot`을 포함한다. Selection에는 actor/time/policy/binding version이 있어야 한다. `RunSnapshot`은 settings snapshot ID, trace/time, template/review, same selection을 저장하고 `OutputVersion`도 동일 selection/context/settings IDs와 digest를 이어받는다. generate 직전 current deployment/digest/capability를 재검증하며 stale이면 provider transport 0이다.

- [ ] **Step 5: truthful Operations/Settings와 workspace isolation을 구현한다**

Modal은 실제 상위 `offlineState`와 Local/Cloud 상태를 받는다. unknown/unavailable은 연결됨으로 표시하지 않는다. Settings는 실제 Provider/model projection과 저장 결과를 사용하며 placeholder 성공을 제거한다. Draft get/edit/queue와 Tauri token은 current Session workspace에 결속하고 Cross Workspace read/write 0을 검증한다.

- [ ] **Step 6: Provider 보안 경계를 재검증한다**

Local Service production composition에는 Ollama만 주입한다. Groq·Upstage credential, endpoint, external adapter instance가 Desktop/Local process에 없음을 source/runtime scan으로 검증한다. Groq·Upstage synthetic actual test는 Cloud API process 안에서만 수행하고 새 공개 API나 production DB mutation을 만들지 않는다.

- [ ] **Step 7: focused GREEN과 관련 회귀를 실행한다**

Local production app HTTP, React actual flow, Rust DTO, workspace isolation, snapshot restart 복원을 먼저 통과시킨 뒤 Task 3·4·6·7 전체 묶음과 diff-check를 실행한다.

---

### Task 3C: Workspace-bound Evidence Resource Provisioning과 Local Evidence

**Files:**
- Modify: `services/local-service/pyproject.toml`
- Create: `services/local-service/src/daon_user_local_service/raw_source.py`
- Modify: `services/local-service/src/daon_user_local_service/local_storage.py`
- Modify: `services/local-service/src/daon_user_local_service/main.py`
- Modify: `services/local-service/src/daon_user_local_service/app.py`
- Create: `services/local-service/tests/test_raw_source.py`
- Modify: `services/local-service/tests/test_offline_studio_http.py`
- Modify: `apps/desktop/src-tauri/src/local_service.rs`
- Modify: `apps/desktop/src-tauri/src/offline_studio_bridge.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Modify: `apps/desktop/src-tauri/tests/offline_studio_bridge_contract.rs`
- Modify: `apps/desktop/src/offline-studio-adapter.js`
- Modify: `apps/desktop/src/offline-studio-pane.jsx`
- Modify: `scripts/tests/offline-studio-ui.test.mjs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`

**Interfaces:**
- Consumes: current Native Session Workspace, Task 3B workspace HMAC request path, `LocalEncryptedStore`, `KnowledgeContextProjector`.
- Produces: 형식 중립 `EvidenceResource` 등록 계약, 현재 `RawSourceService.import_source`, `RawSourceService.list_sources`, internal `GET|POST /local/v1/studio/raw-sources`, Tauri `offline_studio_import_raw_source|offline_studio_list_raw_sources`, actual encrypted `SourceVersion|IndexVersion|EvidenceSpan` projection. 현재 API 이름의 `raw_source`는 사용자·외부 Source origin을 뜻하며 MIME 허용 목록을 뜻하지 않는다.

- [ ] **Step 1: projection-only Raw Source가 실제 생성 근거가 되지 못하는 RED를 작성한다**

Local integration은 Cloud Source ID만 Context에 넣으면 `RAW_SOURCE_NOT_PROVISIONED`와 provider transport 0이어야 한다. 최초 Adapter인 PDF·plain text·Markdown 정상 import는 encrypted object, SourceVersion, IndexVersion, page/segment EvidenceSpan과 exact digest를 생성해야 한다. 이후 웹·표·이미지·음성·영상·DB/API Adapter도 같은 결과 계약을 사용한다. 현재 변환 Adapter가 없는 형식도 원본 Source와 digest는 지식으로 등록·보존하고 `representation_status=not_yet_available`을 표시한다. 선택 LLM이 원본 형식을 직접 처리할 capability도 없으면 해당 Run만 `MODEL_INPUT_CAPABILITY_UNAVAILABLE`와 provider transport 0으로 끝내며 Source 등록·목록·다른 LLM 사용을 막지 않는다. missing/forged Workspace proof, 25MiB 초과, encrypted PDF, malformed UTF-8, digest/idempotency mismatch, cross-workspace list/context는 승인되지 않은 File·Canon·provider write 0을 검증한다.

- [ ] **Step 2: bounded Local parser와 append-only import를 구현한다**

수집 계층은 `EvidenceAdapterRegistry`로 형식을 판별하되 Registry를 Source 허용 목록으로 사용하지 않는다. 모든 Source는 원본과 digest를 먼저 불변 보존하고, 적용 가능한 Adapter가 있으면 동일 bounded Evidence Item과 LLM별 representation을 append한다. 최초 PDF Adapter는 `pypdf==6.14.2`의 `PdfReader(BytesIO(content), strict=True)`로 최대 1,000 pages를 처리하고 page별 nonempty text를 최대 256KiB, 전체 UTF-8 Evidence를 최대 8MiB로 제한한다. text/plain·text/markdown Adapter는 strict UTF-8과 동일 cap을 적용한다. 후속 Adapter도 같은 용량·무결성·Workspace·Canon 경계를 지키며 LLM 경로를 별도로 만들지 않는다. operation lock 안에서 원본 `_put_file`과 SourceVersion을 먼저 append하고, 변환이 가능할 때 `IndexVersion → EvidenceSpan`을 추가한다. 변환 실패는 원본 Source를 삭제하지 않고 representation 상태와 오류만 남긴다. Idempotency replay는 exact Workspace+content digest+metadata만 반환한다.

- [ ] **Step 3: Local command와 Native bridge를 Workspace에 결속한다**

`POST /local/v1/studio/raw-sources`는 filename, content_type, bytes, content_digest_sha256, idempotency_key만 받고 source IDs는 Local Service가 deterministic 생성한다. `GET`은 current Workspace의 successfully indexed Source만 반환한다. 두 method 모두 `studio.write|studio.read`, current workspace HMAC, body cap과 no Browser/Proxy 규칙을 적용한다. Rust command는 current Native Session Workspace를 읽고 bytes를 bounded base64로 Local Service에 전달하며 URL·token·path를 JS에 반환하지 않는다.

- [ ] **Step 4: Studio UI가 provisioned Local Raw Source만 Context에 사용하도록 연결한다**

설정 View에 `Offline Raw Source` file input과 import 상태·목록을 둔다. Cloud Source Pane의 ready 항목을 자동 Raw Source로 승격하지 않는다. import 성공 후 반환된 SourceVersion·digest·quality를 `raw_source` item으로 선택할 수 있고, `raw_only|mixed` confirm은 선택된 provisioned Source만 전송한다. 실패 시 기존 Knowledge/Raw 목록과 draft를 보존하고 Safe error만 표시한다.

- [ ] **Step 5: focused GREEN과 actual encrypted restart Gate를 실행한다**

Python parser/storage/HTTP, Rust command/workspace proof, React actual file import→context confirm→generation transport payload를 검증한다. restart 후 목록·Evidence가 복구되고 plaintext PDF/text가 DB·log·Evidence에 없으며 key lock에서는 list/import/generate 0이다. WSL 사용 시 고유 temp root와 process만 사용하고 공용 Ollama·PostgreSQL 설정은 변경하지 않는다.

---

### Task 4: Command-bound Local API와 Tauri Offline Studio Bridge

**Files:**
- Modify: `services/local-service/src/daon_user_local_service/app.py`
- Create: `services/local-service/tests/test_offline_studio_http.py`
- Modify: `apps/desktop/src-tauri/src/local_service.rs`
- Create: `apps/desktop/src-tauri/src/offline_studio_bridge.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Create: `apps/desktop/src-tauri/tests/offline_studio_bridge_contract.rs`
- Create: `apps/desktop/src/offline-studio-adapter.js`
- Modify: `scripts/tests/desktop-local-service.test.mjs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`
- Modify: `scripts/run-isolated-desktop-cargo.mjs`

**Interfaces:**
- Consumes: Task 3 `OfflineStudioService`; existing `COMMAND_REGISTRY`, `LocalServiceManager`, Tauri `invoke` convention.
- Produces: seven exact Local API commands, Rust command DTOs, `createOfflineStudioAdapter({invoke})`.

- [ ] **Step 1: Route/Capability/JS boundary RED를 작성한다**

```python
@pytest.mark.parametrize(("method", "path", "command"), [
    ("GET", "/local/v1/studio/models", "studio_models_list"),
    ("POST", "/local/v1/studio/knowledge-contexts", "studio_context_prepare"),
    ("POST", "/local/v1/studio/settings/confirm", "studio_settings_confirm"),
    ("POST", "/local/v1/studio/drafts/generate", "studio_draft_generate"),
    ("GET", "/local/v1/studio/drafts/draft-1", "studio_draft_get"),
    ("POST", "/local/v1/studio/drafts/draft-1/versions", "studio_draft_append_version"),
    ("POST", "/local/v1/studio/drafts/draft-1/sync-queue", "studio_sync_queue"),
])
def test_command_registry_is_exact(method, path, command):
    contract = COMMAND_REGISTRY[path]
    assert contract.method == method
    assert contract.command == command
    assert contract.max_body_bytes <= 8 * 1024 * 1024
```

JS test는 adapter source에 `fetch|XMLHttpRequest|WebSocket|localhost|127.0.0.1|NEXT_PUBLIC_`가 없고 exact Tauri commands만 호출함을 단언한다.

- [ ] **Step 2: RED를 실행한다**

Run:

```powershell
Set-Location services/local-service
$env:PYTHONPATH='src;tests'
uv run --isolated --with pytest==9.0.3 --with fastapi --with httpx --with cryptography python -m pytest tests/test_offline_studio_http.py tests/test_security.py -q
Remove-Item Env:PYTHONPATH
Set-Location ..\..
node --test scripts/tests/desktop-local-service.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
```

Expected: commands/routes/adapter 부재로 FAIL.

- [ ] **Step 3: Local API request/response model과 Registry를 구현한다**

각 Pydantic model은 `extra="forbid"`, Safe ID, 최대 Section 수 50, Section title 200자, body 총 1MiB, Output Bundle 8MiB를 적용한다. Model 목록은 opaque deployment ID·safe label·version·readiness만, Knowledge Context는 origin·producer·version·quality·digest만 반환하고 Path·Endpoint를 포함하지 않는다. Dynamic `{id}` route는 기존 exact suffix resolver만 사용하고 wildcard query를 허용하지 않는다. `create_app`은 `offline_studio: OfflineStudioService | None = None`을 받아 미구성 시 `LOCAL_STUDIO_UNAVAILABLE`로 fail-close한다.

- [ ] **Step 4: Rust Bridge를 구현한다**

```rust
#[tauri::command]
pub async fn offline_studio_generate_draft(
    manager: tauri::State<'_, LocalServiceManager>,
    request: GenerateDraftRequest,
) -> Result<OfflineDraftResponse, OfflineStudioBridgeError> {
    manager.request_json("studio_draft_generate", "POST", "/local/v1/studio/drafts/generate", &request, 2 * 1024 * 1024).await
}
```

실제 구현은 manager의 credential snapshot으로 매 요청 새 nonce token을 발급하고 exact Host/Content-Length/timeout/JSON keys를 검증한다. Safe Error code 외 원문 response를 JS로 보내지 않는다.

- [ ] **Step 5: Desktop Adapter를 구현한다**

```javascript
export function createOfflineStudioAdapter({ invoke }) {
  if (typeof invoke !== "function") return null;
  return Object.freeze({
    listModels: () => invoke("offline_studio_list_models"),
    prepareContext: (request) => invoke("offline_studio_prepare_context", { request }),
    confirmSettings: (request) => invoke("offline_studio_confirm_settings", { request }),
    generateDraft: (request) => invoke("offline_studio_generate_draft", { request }),
    getDraft: (draftId) => invoke("offline_studio_get_draft", { draftId }),
    appendEdit: (request) => invoke("offline_studio_append_edit", { request }),
    queueSync: (request) => invoke("offline_studio_queue_sync", { request }),
  });
}
```

- [ ] **Step 6: security negative matrix와 focused GREEN을 실행한다**

Browser headers, proxy headers, wrong host, query, wrong command/capability, replay nonce, oversized body, extra DTO key, response extra key, timeout은 모두 domain write 0이어야 한다.

- [ ] **Step 7: Rust/Node 회귀와 boundary를 실행한다**

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
node --test scripts/tests/desktop-local-service.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
node scripts/verify-product-ui-boundary.mjs --target desktop
```

- [ ] **Step 8: Commit한다**

```powershell
git add services/local-service/src/daon_user_local_service/app.py services/local-service/tests/test_offline_studio_http.py apps/desktop/src-tauri/src/local_service.rs apps/desktop/src-tauri/src/offline_studio_bridge.rs apps/desktop/src-tauri/src/lib.rs apps/desktop/src-tauri/tests/offline_studio_bridge_contract.rs apps/desktop/src/offline-studio-adapter.js scripts/run-isolated-desktop-cargo.mjs scripts/tests/desktop-local-service.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
git commit -m "feat: bridge Windows Offline Studio commands"
```

---

### Task 5: Native Daon Knowledge Provisioning과 Reconnect Sync Orchestration

**Files:**
- Modify: `services/local-service/src/daon_user_local_service/app.py`
- Modify: `services/local-service/src/daon_user_local_service/local_storage.py`
- Create: `services/local-service/tests/test_offline_knowledge_copy_http.py`
- Modify: `services/local-service/tests/test_local_storage.py`
- Modify: `apps/desktop/src-tauri/src/local_service.rs`
- Create: `apps/desktop/src-tauri/src/offline_sync_bridge.rs`
- Modify: `apps/desktop/src-tauri/src/native_session.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Create: `apps/desktop/src-tauri/tests/offline_sync_bridge_contract.rs`
- Create: `apps/desktop/src/offline-sync-adapter.js`
- Create: `scripts/tests/offline-sync-adapter.test.mjs`

**Interfaces:**
- Consumes: 기존 Native session Cookie/CSRF/Step-up, Task 1 다섯 Sync paths, Task 4 command-bound Local API/credential/nonce 경계, `LocalEncryptedStore` AES-GCM·Canon·`sync_queue_states`.
- Produces: Local commands `studio_knowledge_copy_import`, `studio_knowledge_copy_refresh`, `studio_sync_state_read`, `studio_sync_state_append`; Tauri commands `offline_knowledge_list`, `offline_knowledge_provision`, `offline_knowledge_refresh`, `offline_sync_preview`, `offline_sync_status`, `offline_sync_approve`, `offline_sync_transfer`, `offline_sync_resolve`; `createOfflineSyncAdapter({invoke})`.

- [ ] **Step 1: reconnect가 자동 전송하지 않는 RED를 작성한다**

```rust
#[tokio::test]
async fn reconnect_only_exposes_preview_and_never_transfers_without_approval() {
    let result = runtime.on_connectivity_changed(true).await.unwrap();
    assert_eq!(result.awaiting_approval, 1);
    assert_eq!(cloud.transfer_calls(), 0);
}
```

Expired/revoked Step-up, changed Membership/ACL, missing dependency, version conflict, retry restart를 별도 테스트한다.

- [ ] **Step 2: RED를 실행한다**

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
```

Expected: module/commands 부재로 FAIL.

- [ ] **Step 3: Local Knowledge Copy·Sync State 내부 명령 RED를 작성한다**

```python
@pytest.mark.parametrize(("method", "path", "capability", "command", "limit"), [
    ("POST", "/local/v1/studio/knowledge-copies", "knowledge.write", "studio_knowledge_copy_import", 16 * 1024 * 1024),
    ("POST", "/local/v1/studio/knowledge-copies/copy-1/refresh", "knowledge.write", "studio_knowledge_copy_refresh", 32 * 1024),
    ("GET", "/local/v1/studio/sync-operations/sync-1", "sync.read", "studio_sync_state_read", 0),
    ("POST", "/local/v1/studio/sync-operations/sync-1/states", "sync.write", "studio_sync_state_append", 64 * 1024),
])
def test_native_only_local_contract_is_exact(method, path, capability, command, limit):
    contract = resolve_command(path)
    assert (contract.method, contract.capability, contract.command) == (method, capability, command)
    assert contract.max_body_bytes == limit
```

wrong Host/capability/command/method/path, Browser/Proxy Header, query, replay nonce, oversize, malformed Canon, digest mismatch, idempotency reuse, Cross Workspace가 AES File·Canon·Queue write 0임을 실제 Local Service 호출로 고정한다.

- [ ] **Step 4: Local Service encrypted ingest·refresh·state append를 구현한다**

Knowledge import DTO는 Workspace, Copy ID, Package/Producer/Registration/Output Version, authority/review/expiry, schema version, manifest/content SHA-256, canonical Package bytes와 idempotency key만 허용한다. Cloud Package identity는 `package_id + output_version_id + digest_sha256`이며 별도 `package_version: 1`을 만들지 않는다. `producer_version`은 생산 제품 버전, `ScopeSnapshot.version`은 Local append 순번으로 분리한다. Decoded bytes를 12MiB 이하로 제한하고 canonical parse·manifest/content digest를 다시 계산한 뒤 `put_file(..., area="local_private")`와 불변 `ScopeSnapshot`을 같은 operation lock 안에서 수행한다. 실패하면 File·Canon 모두 0이어야 한다.

Refresh는 `approved | revoked | expired`만 허용하고 기존 Copy 원문을 UPDATE하지 않은 채 같은 aggregate의 다음 `ScopeSnapshot` Version을 append한다. 새 Package Version은 import 명령으로만 저장한다. Sync read/append는 기존 `get_sync_queue_state`·`append_sync_queue_state`를 호출하고 자동 Cloud HTTP 요청은 하지 않는다.

- [ ] **Step 5: Native Cloud Client에 기존 다섯 Path만 연결한다**

```rust
const SYNC_PREVIEW_PATH: &str = "/api/v1/workspaces/{workspace_id}/sync-operations";
const SYNC_OPERATION_PATH: &str = "/api/v1/sync-operations/{operation_id}";
const SYNC_APPROVE_PATH: &str = "/api/v1/sync-operations/{operation_id}/approve";
const SYNC_TRANSFER_PATH: &str = "/api/v1/sync-operations/{operation_id}/transfer-batches";
const SYNC_CONFLICT_PATH: &str = "/api/v1/sync-operations/{operation_id}/conflicts/{conflict_id}/resolution";
```

`PUBLIC_GATEWAY` allowlist, HttpOnly session, CSRF와 Idempotency-Key 16..128을 기존 `NativeSessionRuntime` 경계로 재사용한다. JS에 public/internal URL 문자열을 반환하지 않는다.

Knowledge provisioning은 Task 1의 세 Knowledge Package path만 추가로 사용한다. Rust가 package content bytes를 12MiB 이하 bounded memory로 받아 manifest/content digest를 재검증하고 `studio_knowledge_copy_import`에 전달한다. JS는 bytes·URL·Path를 받지 않는다. revoke/expiry refresh는 `studio_knowledge_copy_refresh`, restart Queue 복구는 `studio_sync_state_read|append`를 사용한다.

- [ ] **Step 6: dependency topological order와 approval state를 구현한다**

Source Item을 먼저 전송하고 완료 Target이 확인된 Output Item만 다음 batch에 넣는다. restart 시 Local Queue의 last approved cursor를 읽지만 사용자 `재개` click 전 HTTP transfer는 0이다. Conflict는 자동 merge/overwrite하지 않는다.

- [ ] **Step 7: Desktop JS adapter와 safe state projection을 구현한다**

Adapter는 invoke만 사용하며 Knowledge copy `available|approved|revoked|expired`와 Sync `draft|awaiting_approval|approved|transferring|conflict|reindex_requested` 외 상태를 거부한다. Password/Step-up token은 function-local 변수로만 전달하고 `finally` 후 UI ref/state에서 지운다.

- [ ] **Step 8: actual same-origin-equivalent Native Network Gate를 실행한다**

운영 유사 Docker public origin에 Native Client를 연결해 Daon Knowledge list→`data_area_move` Step-up→encrypted provision→offline read를 먼저 확인하고, reconnect Preview→Step-up→Approve→Source batch→Output batch→reindex 요청을 확인한다. Revoke/expiry refresh 뒤 새 Run 사용 0, Browser request 0, internal URL JS bundle scan 0, approval 전 provider/object transfer 0을 기록한다.

- [ ] **Step 9: Local/Rust/Node 회귀와 plaintext·boundary scan을 실행한다**

```powershell
Set-Location services/local-service
$env:PYTHONPATH='src;tests'
uv run --isolated --with pytest==9.0.3 --with fastapi --with httpx --with cryptography python -m pytest tests/test_offline_knowledge_copy_http.py tests/test_local_storage.py tests/test_offline_studio_http.py -q
Remove-Item Env:PYTHONPATH
Set-Location ..\..
node scripts/run-isolated-desktop-cargo.mjs test
node --test scripts/tests/offline-sync-adapter.test.mjs scripts/tests/desktop-local-service.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
```

Expected: Local command negative Matrix, existing seven Studio commands, existing Native session/recovery tests all PASS; Browser/internal URL scan 0; temp target/gen/test credential remaining 0.

- [ ] **Step 10: Commit한다**

```powershell
git add services/local-service/src/daon_user_local_service/app.py services/local-service/src/daon_user_local_service/local_storage.py services/local-service/tests/test_offline_knowledge_copy_http.py services/local-service/tests/test_local_storage.py apps/desktop/src-tauri/src/local_service.rs apps/desktop/src-tauri/src/offline_sync_bridge.rs apps/desktop/src-tauri/src/native_session.rs apps/desktop/src-tauri/src/lib.rs apps/desktop/src-tauri/tests/offline_sync_bridge_contract.rs apps/desktop/src/offline-sync-adapter.js scripts/tests/offline-sync-adapter.test.mjs
git commit -m "feat: resume approved offline output sync"
```

---

### Task 6: Desktop Offline Studio 상태 모델과 3열 기능 연결

**Files:**
- Create: `apps/desktop/src/offline-studio-model.js`
- Create: `apps/desktop/src/offline-studio-pane.jsx`
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `packages/ui/src/product-workspace-model.js`
- Create: `scripts/tests/offline-studio-ui.test.mjs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`
- Modify: `scripts/tests/product-workspace.test.mjs`

**Interfaces:**
- Consumes: Task 4 `createOfflineStudioAdapter`, Task 5 `createOfflineSyncAdapter`, current `ProductWorkspaceShell` and three panes.
- Produces: `createOfflineStudioState`, `reduceOfflineStudioState`, `OfflineStudioPane`, optional `desktopOfflineStudio` prop on `ProductWorkspaceShell`.

- [ ] **Step 1: 현재 3열 구조 유지와 내부 View 전환 RED를 작성한다**

```javascript
assert.equal(container.querySelectorAll(".workspace-pane").length, 3);
assert.equal(container.querySelector("#product-pane-sources") !== null, true);
assert.equal(container.querySelector("#product-pane-conversation") !== null, true);
assert.equal(container.querySelector("#product-pane-studio") !== null, true);
await click("업무 문서 초안");
assert.equal(container.querySelector("[data-offline-editor]") !== null, true);
assert.equal(container.querySelector("#product-pane-sources") !== null, true);
assert.equal(container.querySelector("#product-pane-studio") !== null, true);
```

입력 mode·Daon Knowledge·Raw Source와 Model 선택 전 생성 disabled, `daon_priority` 지식 없음 safe error, `mixed` origin 표시, `raw_only` warning, 생성→edit→new version, Queue draft, reconnect preview를 실제 React click으로 검증한다.

- [ ] **Step 2: RED를 실행한다**

```powershell
node --test scripts/tests/offline-studio-ui.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/product-workspace.test.mjs
```

Expected: Offline pane/model/optional prop 부재로 FAIL.

- [ ] **Step 3: immutable reducer를 구현한다**

```javascript
export function createOfflineStudioState(overrides = {}) {
  return Object.freeze({
    view: "settings", status: "idle", settingsConfirmed: false,
    context: { mode: "daon_priority", snapshotId: null, items: [], warnings: [] },
    models: [], selectedModelDeploymentId: null,
    draft: null, versions: [], selectedVersionId: null,
    sync: { state: "draft", operationId: null, conflictId: null },
    safeError: null, ...overrides,
  });
}
```

허용 View는 `settings|editor|versions|review|sync`; stale async response는 request revision으로 폐기한다. 실패 시 기존 draft/source/version은 보존하고 safeError/status만 변경한다.

- [ ] **Step 4: ProductWorkspaceShell에 Desktop-only slot을 추가한다**

`desktopOfflineStudio`가 없으면 기존 Web DOM과 event path가 byte-for-byte 동일하다. 있을 때만 가운데 Conversation body를 Editor로 전환하고 오른쪽 Studio 내부 View에 설정·Version·검토·Sync 대기함을 표시한다. Source Pane은 항상 유지한다.

- [ ] **Step 5: OfflineStudioPane Form과 Action 계층을 구현한다**

한 내부 View에 한 Form만 노출한다. 설정 View에서 `Daon 지식 우선 | 혼합 | Raw Source만`을 선택하고 Daon Knowledge Item과 Raw Source를 origin Badge·authority·quality와 함께 표시한다. 적격 Local LLM 목록에서 한 Model을 선택해야 confirm할 수 있다. Offline Cloud Model은 disabled reason을 표시하되 submit payload에 넣지 않는다. Panel당 Primary Action 하나만 filled style hook을 갖는다. 추가 설명은 `i` Tooltip/Popover trigger로 제공하고 상시 설명 Box를 추가하지 않는다. `unverified`는 icon+text+shape로 표시한다.

- [ ] **Step 6: password/token clear와 no-browser-network를 검증한다**

Step-up Password는 uncontrolled ref로 받고 adapter 완료·실패 모두 `finally`에서 빈 값으로 만든다. DOM/bundle/source scan에서 fetch/XHR/WebSocket/internal URL/storage persistence 0을 단언한다.

- [ ] **Step 7: Web 회귀·Desktop GREEN을 실행한다**

```powershell
node --test scripts/tests/offline-studio-ui.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/product-workspace.test.mjs scripts/tests/product-studio.test.mjs
node scripts/verify-product-ui-boundary.mjs --target desktop
```

- [ ] **Step 8: Commit한다**

```powershell
git add apps/desktop/src/offline-studio-model.js apps/desktop/src/offline-studio-pane.jsx apps/desktop/src/desktop-shell.jsx packages/ui/src/product-workspace-shell.jsx packages/ui/src/product-workspace-model.js scripts/tests/offline-studio-ui.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/product-workspace.test.mjs
git commit -m "feat: connect Offline Studio to Windows workspace"
```

---

### Task 7: NotebookLM-inspired Violet 시각 체계와 운영상태·설정 Popup

**Files:**
- Create: `apps/desktop/src/workspace-visual-tokens.css`
- Create: `apps/desktop/src/workspace-modal.jsx`
- Create: `apps/desktop/src/workspace-operations-modal.jsx`
- Create: `apps/desktop/src/workspace-settings-modal.jsx`
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `apps/desktop/src/desktop-shell.css`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `packages/ui/src/workspace.css`
- Create: `scripts/tests/windows-workspace-visual.test.mjs`
- Create: `scripts/tests/windows-workspace-modal.test.mjs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`

**Interfaces:**
- Consumes: Task 3 model/status projection, Task 5 Sync state, Task 6 three-pane Desktop slot.
- Produces: Desktop-scoped visual tokens, `WorkspaceModal`, `WorkspaceOperationsModal`, `WorkspaceSettingsModal`, compact App Bar status.

- [ ] **Step 1: visual hierarchy·popup accessibility RED를 작성한다**

```javascript
assert.equal(container.querySelectorAll(".workspace-panes > section").length, 3);
assert.equal(container.querySelectorAll('[data-variant="primary"]').length <= 3, true);
await click("운영상태");
const dialog = container.querySelector('[role="dialog"][aria-modal="true"]');
assert.equal(dialog.getAttribute("aria-labelledby"), "operations-dialog-title");
assert.equal(document.activeElement, dialog.querySelector("button, [href], input, select, textarea"));
```

Tab trap, Shift+Tab, Escape, Background inert, opener focus return, dirty settings close confirm, critical inline alert를 실제 React behavior로 검증한다.

- [ ] **Step 2: RED를 실행한다**

```powershell
node --test scripts/tests/windows-workspace-visual.test.mjs scripts/tests/windows-workspace-modal.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
```

Expected: tokens/modals/App Bar actions 부재로 FAIL.

- [ ] **Step 3: Desktop-scoped visual tokens를 구현한다**

```css
.desktop-shell[data-visual-system="notebook-violet"] {
  --workspace-canvas: #f6f5f9;
  --workspace-surface: #ffffff;
  --workspace-surface-raised: #fbfaff;
  --workspace-border: #ddd9e8;
  --workspace-text: #211d2d;
  --workspace-muted: #6f687c;
  --workspace-accent: #6952d9;
  --workspace-accent-strong: #5139bf;
  --workspace-danger: #b42318;
  --workspace-radius: 13px;
  --workspace-shadow: 0 8px 24px rgb(37 28 70 / 8%);
}
```

Dark Theme은 별도 opaque surface/token으로 WCAG AA를 만족한다. Accent는 primary/selected/citation/status에만 사용하며 gradient와 장식용 대형 illustration을 금지한다. Transition은 150~200ms, reduced-motion에서는 0ms이다.

- [ ] **Step 4: 현재 3열 Pane의 시각 층만 개선한다**

각 Pane에 16px line icon+14px title, 13px radius, 얇은 border/shadow를 적용한다. HTML 기본 input/button을 명확한 height/padding/focus ring/disabled state로 정렬한다. Source 선택·Citation·Version·Sync 상태는 icon+text+shape 세 가지를 함께 사용한다. 레이아웃 순서와 기능 위치는 바꾸지 않는다.

- [ ] **Step 5: App Bar와 운영상태 Modal을 구현한다**

App Bar에는 `정상|주의|오류` + `Offline|Cloud 연결` compact status, `운영상태`, `설정` Button만 둔다. 운영상태 Modal은 Local Service, encrypted storage, Ollama 연결·선택 모델, Cloud Sync, pending jobs, last checked time, safe action을 표시한다. 오류 원문/내부 주소는 노출하지 않는다.

- [ ] **Step 6: 설정 Modal과 unsaved guard를 구현한다**

Provider/Model, default output format, Version save mode, Sync approval mode를 grouped fieldset으로 표시한다. Offline에서는 eligible Ollama 모델만, Online test/operation에서는 승인된 Groq·Upstage Deployment를 구분해 표시한다. Organization-enforced RuleSet/review/Egress는 read-only lock icon+text로 표시한다. Dirty close는 confirm view를 Modal 내부에 표시하고 Save/Discard/Continue editing 중 하나를 선택하기 전 닫지 않는다.

- [ ] **Step 7: 상시 설명 Box와 전역 하단 오류를 제거한다**

긴 description은 `i` tooltip/popover로 이동한다. Safe Error는 관련 Panel inline alert와 App Bar status로 표시한다. Critical이면 해당 Action disabled를 유지하며 운영상태 Modal에서 safe recovery action을 제공한다.

- [ ] **Step 8: 1920×1080·responsive·Light/Dark 실제 Browser Gate를 실행한다**

1920×1080에서 세 Pane이 viewport 안에 있고 horizontal scroll 0, 1366×768에서 조작 가능, 768 이하에서 순서 Source→Chat/Editor→Studio 유지, 200% zoom keyboard access 가능을 screenshot/DOM으로 확인한다. Popup open/close/focus와 internal URL scan 0을 기록한다.

- [ ] **Step 9: Web DOM 불변·build·boundary를 검증한다**

```powershell
node --test scripts/tests/windows-workspace-visual.test.mjs scripts/tests/windows-workspace-modal.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/product-workspace.test.mjs
node scripts/verify-product-ui-boundary.mjs --target desktop
Set-Location apps/web
npm run build
```

Expected: Web optional prop 미사용 시 기존 3-pane DOM/action tests PASS; Next compile/TypeScript/boundary PASS.

- [ ] **Step 10: Commit한다**

```powershell
git add apps/desktop/src/workspace-visual-tokens.css apps/desktop/src/workspace-modal.jsx apps/desktop/src/workspace-operations-modal.jsx apps/desktop/src/workspace-settings-modal.jsx apps/desktop/src/desktop-shell.jsx apps/desktop/src/desktop-shell.css packages/ui/src/product-workspace-shell.jsx packages/ui/src/workspace.css scripts/tests/windows-workspace-visual.test.mjs scripts/tests/windows-workspace-modal.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
git commit -m "feat: polish Windows workspace visual system"
```

---

### Task 8: End-to-End Verification, Work Order Evidence와 Windows Gate

**Files:**
- Create: `docs/02_work_orders/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_work_order.md`
- Create: `docs/02_work_orders/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_prompt.md`
- Create: `docs/04_test_reports/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_progress.md`
- Create: `docs/04_test_reports/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_completion_report.md`
- Create: `docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/manifest.json`
- Create: `scripts/tests/windows-offline-studio-evidence-hash.test.mjs`
- Modify only if verifier inputs require it: `scripts/tests/product-ui-boundary.test.mjs`

**Interfaces:**
- Consumes: Tasks 1–7 all public/internal contracts and the approved design/plan.
- Produces: reproducible Work Order, minimal prompt, timestamped progress, completion report, hashed actual PG/Windows/Network/UI evidence.

- [ ] **Step 1: Work Order와 진행 복구 계약을 작성한다**

Work Order는 설계·계획의 경로와 commit을 지정하고 구현 내용을 중복하지 않는다. Prompt는 다음 실행 명령만 포함한다.

```markdown
`AGENTS.md`, 승인 상세 설계서, 구현계획과 `R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_work_order.md`를 EOF까지 읽고 단일 Writer로 TDD 수행하라. 각 단계와 오류·복구·테스트·변경 파일을 지정 Progress에 기록하고 결과 계약으로 종료하라.
```

- [ ] **Step 2: 전체 자동 검증을 fresh runner로 실행한다**

```powershell
Set-Location services/local-service
$env:PYTHONPATH='src;tests'
uv run --isolated --with pytest==9.0.3 --with fastapi --with httpx --with cryptography --with pydantic python -m pytest tests -q
Remove-Item Env:PYTHONPATH
Set-Location ..\api
$env:PYTHONPATH='src;tests'
uv run --isolated --with pytest==9.0.3 --with argon2-cffi --with httpx --with "psycopg[binary]==3.3.4" --with psycopg-pool --with fastapi --with minio python -m pytest tests -q
Remove-Item Env:PYTHONPATH
Set-Location ..\..
node --test scripts/tests/offline-sync-adapter.test.mjs scripts/tests/offline-studio-ui.test.mjs scripts/tests/windows-workspace-visual.test.mjs scripts/tests/windows-workspace-modal.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/product-workspace.test.mjs scripts/tests/openapi-contract.test.mjs
Set-Location apps/desktop/src-tauri
cargo test
Set-Location ..\..\..
node scripts/verify-openapi-contract.mjs
node scripts/verify-product-ui-boundary.mjs --target desktop
Set-Location apps/web
npm run build
```

- [ ] **Step 3: 실제 PostgreSQL 15/18 Gate를 수행한다**

고유 disposable DB에서 0001→0014, 0013→0014, legacy Source operation replay, Output dependency/order/import/idempotency/RLS, downgrade fail-close, output cleanup 후 rollback/reapply를 실행한다. 명령·exit·counts·SQLSTATE는 secret-free transcript에 저장하고 DB prefix remaining 0을 확인한다.

- [ ] **Step 4: Groq·Upstage actual generation과 Ollama installed-model connection Gate를 수행한다**

정제된 테스트 Knowledge Snapshot을 사용해 Groq와 Upstage를 각각 명시 선택하고 settings confirm→draft generation→Citation/grounding→RunSnapshot·OutputVersion 저장을 실제 호출한다. 두 Provider 모두 출력 Schema exact, Citation source/version/digest 일치, 내부 URL·credential 노출 0, 자동 fallback 0을 검증한다.

별도 Ollama Gate에서는 현재 Ollama `/api/tags`와 `/api/show`로 설치된 completion 모델을 조회하고 exact name/digest 선택, `/api/chat` 내부 호출, RunSnapshot 결속, 외부 Groq·Upstage transport 0을 확인한다. `:cloud`, remote-host, embedding-only 모델은 목록/실행 0이어야 한다. Ollama 모델 설치·삭제는 수행하지 않는다. Groq·Upstage 성공만으로 Ollama 오프라인 Gate PASS를 선언하지 않는다.

- [ ] **Step 5: reconnect 승인 Sync Gate를 수행한다**

재연결만으로 transfer 0을 먼저 확인한 뒤 UI에서 Preview→Step-up→Approve→Resume를 실제 click한다. Cloud API는 기존 다섯 Path만 호출되고 Source dependency 후 Output import, target version, reindex request, Audit가 생성되어야 한다. 승인 만료·권한 축소·conflict에서는 transfer/write 0을 별도 확인한다.

- [ ] **Step 6: 1920×1080 visual/operations/settings Gate를 수행한다**

현재 3열 구조 유지, Violet hierarchy, panel primary action, Source/Editor/Studio View, compact status, Operations Modal, Settings Modal, dirty guard, focus trap, Escape, focus return, reduced motion, Light/Dark, 200% zoom을 actual Desktop WebView에서 확인한다. 화면 추가 시 동일 design system을 재사용할 수 있는 token/class API도 manifest에 기록한다.

- [ ] **Step 7: 보안·same-origin/loopback boundary를 검증한다**

Browser Network에는 internal URL/localhost/127.0.0.1/Docker host 0건, Desktop JS bundle에도 해당 문자열 0건이어야 한다. Loopback은 Rust process만 호출하며 browser/proxy/replay/oversize negatives가 domain write 0임을 확인한다. 로그/화면/evidence의 token/password/path/stack/SQLSTATE scan 0을 기록한다.

- [ ] **Step 8: Evidence hash와 diff boundary를 닫는다**

Manifest는 commit SHA, migration revision, test counts, actual DB/Windows/network/browser artifacts와 SHA-256을 포함한다. Hash test는 선언된 모든 artifact의 current bytes를 검증한다.

```powershell
node --test scripts/tests/windows-offline-studio-evidence-hash.test.mjs
git diff --check
git status --short
```

Expected: artifact mismatch 0, staged 0, protected unrelated dirty unchanged.

- [ ] **Step 9: 문서만 별도 Commit하고 최종 검토를 요청한다**

```powershell
git add docs/02_work_orders/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_work_order.md docs/02_work_orders/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_prompt.md docs/04_test_reports/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_progress.md docs/04_test_reports/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_completion_report.md docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/manifest.json scripts/tests/windows-offline-studio-evidence-hash.test.mjs
git commit -m "docs: record Windows Offline Studio verification"
```

최종 보고는 `판정 → 판단 이유 → 조치` 순서이며 자동 테스트, actual PostgreSQL, Groq/Upstage actual generation, Ollama installed-model connection, actual UI/Network, 외부 배포를 구분한다. `COMPLETED`는 모든 actual Gate가 닫힌 경우에만 사용한다.

---

## Plan Self-Review

- Spec coverage: Daon Knowledge 우선+Raw Source 이중 입력, selectable eligible Local LLM, immutable Context/Model snapshots, Local Domain, real managed model execution, encrypted Canon, Loopback/Tauri, reconnect Sync, public contract/Migration 0014, Cloud import, current three-pane layout, NotebookLM-inspired Violet, Operations/Settings Modal, future screen reuse, security and actual Gates are each assigned to Tasks 1–8.
- Existing behavior: legacy Source Sync defaults, five public Paths, Web Workspace optional behavior, current pane positions, existing storage/queue and no automatic reconnect transfer are explicitly protected.
- Scope boundary: model download/install lifecycle, unrelated Web/settings/mobile redesign, new public Sync Path, new plaintext store and automatic Cloud approval are excluded.
- Type consistency: `SyncItemKind.OUTPUT_VERSION`, `output_version_id`, `dependency_item_ids`, `OfflineStudioOutputBundle`, `KnowledgeContextSnapshot`, `ModelSelectionSnapshot`, `OfflineStudioService`, `OfflineDraftView`, `createOfflineStudioAdapter`, `createOfflineSyncAdapter`, `desktopOfflineStudio` use the same names across producing and consuming tasks.
- Placeholder scan: 금지된 임시 표식, 불명확한 오류 처리, 이름 없는 테스트와 구현 생략용 Ellipsis는 없다. `tuple[T, ...]`는 Python variadic tuple type이고 `{ ...overrides }`는 JavaScript object spread다.

---

## 2026-08-14 완료 기록 — R1-M8-10-WEB-FINAL-UI-I001

이 절은 최종 화면 Shell을 먼저 구현·배포한 완료 기록이다. 이후 개발 순서는 상단 `Foundation-first·Menu-by-menu 실행 순서`가 대체한다.

1. 현재 제품 callback·same-origin BFF·Source·Question·Studio 상태를 보존하는 실제 React RED를 작성한다.
2. 1920×1080 최종 Workspace App Bar, Source List, Conversation Transcript/Composer, Studio 3열 Tile Grid와 산출물 Library를 구현한다.
3. 보고서 Tile 선택 후 목적·독자·분량·구성·출력 형식·검토 조건·모델·정책 요약을 표시하는 생성 설정 View를 구현한다.
4. 생성 중·안전 실패·완료와 저장 산출물 상세를 같은 화면 위계로 구현한다.
5. App Bar 설정 Menu와 `LLM 설정` Popup에서 9 Provider 상태와 선택 Provider 상세를 표시하고 기존 실제 Provider 설정 경계만 연결한다.
6. 관련 DOM/actual React, Product Workspace·Studio·Provider Settings, lint, Web Build·TypeScript, Boundary, same-origin·내부정보 scan을 수행한다.
7. 어울1 독립 검토 후 해당 UI diff만 exact stage·commit·push한다.
8. ysna-server에서 기존 Web image rollback tag와 서비스 ID를 고정한 뒤 Web만 rebuild/recreate한다. API·Worker·DB·공용 서비스는 변경하지 않는다.
9. 로그인된 실제 Browser에서 기본·보고서 설정·LLM 설정·생성/완료 상태와 Source·질문·Studio 회귀, same-origin Network를 검증한다.

이 완료 기록은 Task 1~8의 기존 산출물을 폐기하지 않는다. 이후에는 공통 모듈·공통 API를 먼저 검증하고 메뉴별 수직 기능으로 연결한다.

---

## 2026-08-20 Phase E Windows actual 후속 순서

Phase E 코드 독립 검토는 `CODE_VERIFIED`로 종료했다. 다음 기능으로 이동하기 전에 별도 계획 `docs/superpowers/plans/2026-08-20-windows-webview-execution-recovery.md`를 수행한다. 순서는 실패 Ledger/성공 경로 대조 → 최소 Tauri Window/WebView smoke → 단일 root cause 교정 → 실제 Windows production flow → fresh 회귀·독립 검토다. 동일 실패 launch 반복과 기능 우회는 금지한다.

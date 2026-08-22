# NotebookLM Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 현재 Workspace 화면을 유지하면서 NotebookLM의 Source·대화·Studio·Library 기능과 MCP/Daon 승인 지식 연결을 운영형으로 구현한다.

**Architecture:** 일반 파일·웹·Drive·텍스트 Source와 연결형 MCP/Daon Source를 동일한 Notebook Source 선택 흐름으로 노출하되 저장 수명과 Connector 계층은 분리한다. 기존 same-origin BFF와 현재 Studio 카드 배치를 유지하고, 각 Studio 카드를 실제 백그라운드 생성 작업과 Library 결과로 연결한다.

**Tech Stack:** Existing FastAPI/PostgreSQL/Object Storage/Worker, Next.js BFF, React workspace UI, existing provider/model routing and audit contracts.

**Spec:** `docs/superpowers/specs/2026-08-22-notebooklm-parity-design.md`

## Global Constraints

- 화면 배치와 카드 위치는 변경하지 않는다.
- PDF만 지원하도록 제한하지 않는다.
- Source 등록·삭제는 사용자 확정 즉시 처리한다.
- 외부 원본 소실은 자동 삭제하지 않고 `사용 불가`로 표시한다.
- Source 등록 시 `Notebook과 함께 삭제`와 `Notebook 삭제와 무관하게 보관`을 선택한다.
- 동일한 파일 내용은 Digest로 중복 저장하지 않는다.
- MCP와 Daon 승인 지식은 연결형 Source로 구현한다.
- 브라우저 코드에는 API 절대주소·localhost·Docker 내부 주소를 넣지 않는다.
- 구현 전 설계서와 본 계획의 신산님 승인이 필요하다.
- 로컬 검증 → Git Push → ysna-server 배포·통합검증 → 운영 배포 승인 순서를 지킨다.

### Task 1: Source 계약과 수명주기 정합화

**Files:**
- Modify: `services/api/src/daon_user_api/source_ingest.py`
- Modify: `services/api/src/daon_user_api/source_upload.py`
- Modify: `services/api/src/daon_user_api/data_canon.py`
- Modify: `apps/web/lib/product-workspace-api.js`
- Modify: `packages/ui/src/notebook-context-adapter.js`
- Test: `services/api/tests/test_source_lifecycle_contract.py`

- [ ] Source 유형·원본 참조·Notebook 귀속·`unavailable` 상태 계약을 테스트로 고정한다.
- [ ] 등록 시 `delete_with_notebook`/`retain_after_notebook_delete` 삭제 정책을 저장한다.
- [ ] PDF 전용 검사를 지원 Matrix 기반 검사로 바꾼다.
- [ ] 사용자 삭제는 즉시 Notebook Source와 관련 인덱스를 제거하도록 연결한다.
- [ ] `content_digest` 기반 원본 중복 저장 방지와 Notebook별 독립 binding을 구현한다.
- [ ] Notebook 삭제 시 선택한 삭제 정책과 다른 Notebook binding이 남은 원본 보존을 검증한다.
- [ ] 외부 원본 소실은 Source를 보존하고 `unavailable`로 반환한다.
- [ ] Source 목록·삭제·재등록 API 계약 테스트를 실행한다.

### Task 2: NotebookLM형 Source 추가 화면 연결

**Files:**
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `packages/ui/src/notebook-context-adapter.js`
- Modify: `apps/web/app/notebooks/[notebookId]/page.jsx`
- Test: `scripts/tests/notebook-source-add-flow.test.mjs`

- [ ] 현재 Source 패널 배치를 유지한다.
- [ ] 파일 업로드·웹사이트·Drive·복사 텍스트·검색 결과 선택 흐름을 동일한 추가 UI로 연결한다.
- [ ] 선택한 검색 결과만 등록하고 선택하지 않은 결과는 저장하지 않는다.
- [ ] 등록 중·완료·실패·사용 불가 상태를 화면에 표시한다.
- [ ] 등록과 삭제의 실제 same-origin Network 경로를 검증한다.

### Task 3: MCP 및 Daon 승인 지식 Connector

**Files:**
- Create: `services/api/src/daon_user_api/mcp_connector.py`
- Modify: `services/api/src/daon_user_api/approved_knowledge_connector.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Modify: `apps/web/lib/product-workspace-api.js`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Test: `services/api/tests/test_mcp_connector.py`

- [ ] 공통 Connector 등록·상태·재연결·해제 계약을 정의한다.
- [ ] 국가법령정보센터 `https://open.law.go.kr/` 샘플 Connector를 구현한다.
- [ ] 인증정보·쿼터·외부 주소를 BFF/서버에만 둔다.
- [ ] Daon 승인 지식도 같은 Source 선택 인터페이스로 노출한다.
- [ ] Connector 장애와 원본 소실을 `unavailable`로 표시하고 자동 삭제하지 않는다.

### Task 4: NotebookLM형 대화·근거 동작

**Files:**
- Modify: `services/api/src/daon_user_api/question_answering_service.py`
- Modify: `services/api/src/daon_user_api/question_answering_postgres.py`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Test: `services/api/tests/test_notebooklm_chat_grounding.py`

- [ ] 선택 Source 기반 질문과 일반 업무 상담을 분리한다.
- [ ] 답변에 가능한 인라인 Citation을 연결한다.
- [ ] Source 미선택 일반 질문에 근거 부족 고정문구를 사용하지 않는다.
- [ ] 사용 불가 Source는 검색·대화 컨텍스트에서 제외한다.
- [ ] 한국어 출력과 일반적인 업무 질문 회귀 테스트를 추가한다.

### Task 5: Studio 산출물 기능 정합화

**Files:**
- Modify: `services/api/src/daon_user_api/studio_workspace.py`
- Modify: `services/api/src/daon_user_api/studio_export.py`
- Modify: `services/api/src/daon_user_api/report_generation.py`
- Modify: `packages/ui/src/studio-workflow-pane.jsx`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Test: `services/api/tests/test_notebooklm_studio_outputs.py`

- [x] 현재 카드 배치를 유지하면서 11개 Studio 기능의 기능·입력·출력 계약을 고정한다.
- [x] 생성 전 Source·언어·형식·길이·사용자 지시 설정을 받는다.
- [x] Audio/Video를 제외한 9개 구조화 출력과 기존 업무 산출물을 백그라운드 작업으로 생성한다. Audio/Video는 provider 미연결 시 `unavailable`로 종료한다.
- [x] 생성 결과를 기존 Library 저장·열기·다운로드·삭제 계약에 연결한다.
- [x] 결과물별 Source 계보와 생성 시각을 보존한다.

비동기 구현 메모: 기존 공통 Studio 작업 큐 계약이 없어 `0024_studio_generation_jobs`, same-origin 상태 조회 API, `studio_generation_worker`를 최소 추가했다. 기존 동기 `create_generation`과 Library 저장 경로는 worker가 재사용하며, 완료를 가장하지 않는다.

### Task 6: 통합 검증과 ysna-server 배포

**Files:**
- Create: `docs/04_test_reports/release_1/R1-NOTEBOOKLM-PARITY-I001_progress.md`
- Create: `docs/04_test_reports/release_1/R1-NOTEBOOKLM-PARITY-I001_completion_report.md`
- Modify: `docs/02_work_orders/release_1_traceability.md`

- [ ] 로컬 단위·계약·빌드 테스트를 실행한다.
- [ ] Git Commit SHA와 변경 범위를 기록한다.
- [ ] ysna-server 격리 환경에 배포하고 DB·Object Storage·Worker·BFF Health를 확인한다.
- [ ] 실제 브라우저에서 Source 등록·삭제, MCP 연결, 대화, Studio 생성·Library 조회를 클릭 검증한다.
- [ ] 운영 배포는 신산님 최종 승인 전에는 수행하지 않는다.

## 완료 기준

- NotebookLM Source 추가 흐름과 지원 유형이 동작한다.
- MCP·Daon 승인 지식이 연결형 Source로 동작한다.
- Source 등록·삭제가 즉시 반영된다.
- 원본 소실은 `사용 불가`로 표시되고 자동 삭제되지 않는다.
- 대화가 Source 기반/일반 상담을 구분한다.
- Studio 카드가 실제 산출물과 Library 결과로 연결된다.
- 로컬·ysna-server·브라우저 검증 증거가 모두 기록된다.

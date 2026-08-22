# R1-M8-10-WINDOWS-OFFLINE-STUDIO-01 작업지시서

## 판정

신산님은 2026-08-14 Windows Offline Studio 상세 설계와 구현계획, 그리고 현재 `codex/user-auth-screen-split` 브랜치를 이번 작업의 실행 브랜치로 사용하는 예외를 승인했다.

Task 5 RED에서 Cloud Knowledge Package bytes를 Local 암호화 저장소와 Sync Queue에 전달할 내부 명령이 계획에 누락됐음이 확인됐다. 신산님은 2026-08-14 이 보안 경계 보완을 승인했다. 설계·계획에 추가된 Tauri 전용 Local Service 명령 4종은 본 작업 범위이며 Browser 공개 API·Cloud Sync Path 추가로 해석하지 않는다.

목표는 Daon2·2.5·3에서 등록된 고급 지식을 기본·우선 입력으로 사용하면서 Raw Source도 명시적으로 선택할 수 있고, 다른 LLM과 동일한 Provider/Deployment 계약으로 Ollama에 이미 설치된 로컬 모델을 선택해 실제 오프라인 답변·초안을 생성하는 Windows 운영형 Studio를 완성하는 것이다. NotebookLM과 유사한 Source-grounded 사용 흐름을 출발점으로 삼되 지식 권위·Version·Citation·승인·Sync 계보를 Daon 계약으로 보존한다. 실제 생성 기능·품질 검증은 Groq와 Upstage를 각각 사용하고 Ollama 오프라인 연결 검증과 분리한다.

최종 화면 Shell은 구현·ysna Web 배포를 완료한 기준선으로 유지한다. 이후 수행은 신산님이 2026-08-14 확정한 `공통 모듈·공통 API 우선 → 화면 메뉴 하나씩 수직 완성` 순서를 따른다. 대표 기능 시험은 `UPSTAGE | GROQ | MISTRAL` 중 하나만 사용하고, 필요할 때만 두 번째 Provider로 호환성을 확인한다.

## 작업 계약

| 항목 | 내용 |
| --- | --- |
| issue_id | `R1-M8-10-WINDOWS-OFFLINE-STUDIO-01-I001` |
| 승인 설계 | `docs/superpowers/specs/2026-08-14-windows-offline-studio-draft-design.md` |
| 구현계획 | `docs/superpowers/plans/2026-08-14-windows-offline-studio-draft.md` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_progress.md` |
| 완료 보고 | `docs/04_test_reports/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_completion_report.md` |
| Evidence | `docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/` |
| 개발자 | 어울2 `daon-developer` 단일 Writer |
| 승인 브랜치 예외 | `codex/user-auth-screen-split` |

## 필수 수행

1. `AGENTS.md`, 승인 설계, 구현계획, 본 작업지시서와 실행 프롬프트를 EOF까지 읽고 각 SHA-256, Git root·branch·origin·HEAD·staged 상태를 Progress에 기록한다.
2. 구현계획의 Phase A `A1→A6` 공통 모듈·API Gate를 먼저 닫은 뒤 Phase B 메뉴를 명시 순서대로 하나씩 수행한다. 한 메뉴의 Domain·DB·API/BFF·UI·actual Browser Gate가 닫히기 전 다음 메뉴를 시작하지 않는다.
3. 입력은 `Daon 지식 우선 | 혼합 | Raw Source만` 세 모드를 모두 구현한다. Daon2·2.5·3 Knowledge Package가 기본·우선이며 Raw Source를 제거하거나 자동으로 등록 지식으로 승격하지 않는다.
4. Ollama Provider 실행 어댑터를 구현한다. Daon이 모델을 설치·삭제하거나 임의 실행파일을 등록하지 않는다. `/api/tags`·`/api/show`로 이미 설치된 completion 모델의 exact name/digest/capability를 검증하고 `/api/chat`으로 실행한다. `:cloud`, remote-host, embedding-only 모델은 오프라인 후보에서 제외한다.
4-1. 실제 생성 기능·품질 Gate는 `UPSTAGE | GROQ | MISTRAL` 중 대표 하나를 명시 선택해 동일 Knowledge Context·출력 Schema·Citation 검증으로 수행한다. 호환성 문제가 있을 때만 두 번째 Provider를 추가하며 전체 반복 시험을 금지한다. Provider 자동 fallback은 0으로 유지한다.
4-2. `main.py` 제품 조립, current Session workspace-bound Local DTO, Desktop `prepareContext→confirmSettings→generateDraft→edit→queueSync`, Settings/Run/Output의 동일 Provider selection 계보와 실제 Operations/Settings 상태를 Task 3B로 구현한다. 테스트 fixture 주입만으로 제품 연결 PASS를 선언하지 않는다.
4-3. Groq·Upstage credential/adapter는 Cloud API 프로세스에만 둔다. Desktop/Local Service로 자격이나 external transport를 반입하지 않으며 새 Cloud Studio 공개 API는 본 Work Order에서 추가하지 않는다.
4-4. Raw Source는 Cloud Source 목록의 ID만 Local Context에 넣지 않는다. Desktop에서 사용자가 명시 선택한 PDF·plain text·Markdown bytes를 current Native Session Workspace와 HMAC proof에 결속해 Local Service 내부 전용 command로 import하고, 암호화 원본과 SourceVersion·IndexVersion·EvidenceSpan Canon이 모두 저장된 항목만 Offline Studio에 노출한다. PDF parser는 `pypdf==6.14.2`로 고정하며 임의 subprocess·Cloud API·외부 Provider 호출은 0이다.
5. Knowledge Context와 Model 선택을 각각 불변 Snapshot으로 결속하고 Citation·OutputVersion·RunSnapshot·Sync 계보에 동일 ID와 digest를 유지한다.
6. 현재 배포된 NotebookLM-inspired Violet 3열 구조, Studio Grid·Library, App Bar·Popup을 화면 기준선으로 유지한다. 공통 모듈/API를 먼저 연결하고 이후 LLM 설정→Source→대화→Studio 유형→Library→운영상태→나머지 설정 메뉴 순으로 기능을 완성한다.
7. 브라우저는 same-origin BFF만 사용하고 Local Service는 Tauri command-bound 경계로만 호출한다. 내부 URL·Port·Token·Path·Stack·SQLSTATE를 UI·로그·Evidence에 노출하지 않는다.
7-1. Knowledge Copy import/refresh와 Sync state read/append는 승인 설계의 내부 명령 4종만 사용한다. 명령별 capability·body cap·canonical/digest 재검증·Cross Workspace write 0을 적용하고 일반 Storage 명령이나 Browser 경로로 우회하지 않는다.
8. 로컬 자동 검증뿐 아니라 actual PostgreSQL, 실제 Windows 설치 모델 2종, process/network, Desktop WebView, reconnect 승인 Sync Gate를 수행하고 서로 구분된 Evidence를 남긴다.
9. 계획 안의 오류는 원인을 진단하고 승인된 방식을 유지해 해결하며 계속한다. 계획 밖 공개 API·데이터 계약·보안 경계·의존성·파괴적 작업·외부 배포가 필요할 때만 수정 전에 `BLOCKED`로 보고한다.
10. 어울2는 commit·push·merge·배포하지 않는다. 보호 dirty를 stage·restore·delete하지 않고 관련 없는 파일을 수정하지 않는다.

## 허용 범위

구현계획 Phase A–E와 Tasks 0–8의 `Files`에 명시된 파일, 그리고 다음 진행·완료·Evidence 경로만 허용한다. 실행 순서는 Phase A→B→C→D→E이며 기존 Task 번호는 각 Phase의 세부 구현·검증 절차로 사용한다.

- `docs/02_work_orders/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_work_order.md`
- `docs/02_work_orders/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_prompt.md`
- `docs/04_test_reports/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_progress.md`
- `docs/04_test_reports/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_completion_report.md`
- `docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/**`

실제 코드와 계획의 파일 경로가 다르면 먼저 증거와 영향 범위를 Progress에 기록하고 어울1에게 보고한다. 요구사항과 외부 동작을 바꾸지 않는 내부 경로 교정은 어울1 판단 대상이며, 범위 변경은 신산님 승인 대상이다.

## 보호해야 하는 현재 상태

- 정본 Root: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`
- 승인 실행 Branch: `codex/user-auth-screen-split`
- 재착수 HEAD: `fa5e042` (`docs: reorder notebook delivery and manual production`)
- 기존 Desktop·Mobile·Web Settings·타 작업 문서의 dirty/untracked 변경은 사용자 자산이므로 미접촉한다.
- 기존 Web Workspace optional prop 미사용 동작, Source Sync 기본값, Cloud 공개 API, SQLCipher/Queue, no automatic reconnect transfer를 유지한다.

## 금지

- Daon2·2.5·3 내부 DB·Module·File Path 직접 결합
- Raw Source 제거 또는 무조건 Knowledge Snapshot으로 강제 변환
- Fixture-only 또는 Groq·Upstage 결과만으로 Ollama 오프라인 Gate 통과 선언
- Daon의 Ollama 모델 설치·삭제, 임의 executable 등록·실행
- Offline에서 Cloud Model 성공 표시, 자동 Provider fallback, 자동 승인·자동 전송
- Browser의 API 절대주소·localhost·127.0.0.1·Docker Host 직접 호출
- 별도 평문 Queue·별도 임시 SQLite·암호화 저장소 우회
- 현재 3열 구조를 Dashboard/Cockpit으로 전면 재배치
- 상시 설명 Box, 전역 하단 대형 오류 Box, 과도한 장식·Gradient
- 관련 없는 리팩터링, 설정값 임의 변경, 보호 변경 stage/restore/delete
- 어울2의 commit·push·merge·배포

## 필수 검증 및 결과 계약

구현계획의 Task별 RED/GREEN과 전체 검증 명령을 실행하고 다음을 구분해 보고한다.

- 정적 계약·Unit/Integration Test
- API·Local Service 전체 회귀
- Web Build·TypeScript·Boundary
- actual PostgreSQL Migration/RLS/rollback/reapply
- A1 remediation: `0015_security_audit_step_up_idempotency` 전용 Security Audit persistence를 0014 뒤에 적용하고, Step-up 발급·소비 원장은 Identity 저장소 한 곳에서 원자적으로 관리한다. 공개 API·DTO 변경 없이 Session/ACL/Step-up durable Audit, idempotency replay-before-consume, PG15/18 cross-tenant write0 및 secret-free transcript/hash를 닫는다. 사용되지 않는 PostgreSQL 이중 원장은 금지한다.
- Step-up exact replay는 `DAON_STEP_UP_TOKEN_KEY_FILE` root-owned reference의 versioned HMAC만 사용한다. raw/ciphertext 저장은 금지하며 production key absent/rotation pending replay는 fail-close한다.
- A2 remediation: `0016_output_version_content_lineage`에서 OutputVersion 내용 `content_version`과 상태 전이 `version`을 분리한다. same-key 동시 Version/Action은 advisory lock 후 replay하며 최신 Version은 content_version 기준으로 결정한다. 실제 다중 Version·RLS·FK·replay 1건·rollback fail-close·reapply를 PostgreSQL에서 검증한다.
- 대표 `UPSTAGE | GROQ | MISTRAL` 하나의 actual generation과 Citation/RunSnapshot; 필요 시 두 번째 Provider 호환성
- Ollama는 설치된 completion model의 조회·선택·연결 계약을 다른 Provider와 같은 방식으로 확인하되 대표 생성 기능 전체 반복 시험 대상으로 강제하지 않는다.
- actual Desktop UI 1920×1080·Light/Dark·Modal Focus·same-origin Network
- reconnect Preview→Step-up→Approve→Resume와 deny/conflict write 0
- Evidence manifest hash, `git diff --check`, staged 0, 보호 변경 불변

종료 보고 형식은 다음과 같다.

Phase E 추가 승인 계약은 selected Context 공개 GET과 exact same-origin BFF, Source·Question·Studio read/write의 필수 canonical `notebook_id`, Tenant/Workspace/Notebook Binding 검증과 생성 Resource의 동일 Notebook 원자 귀속이다. missing·invalid·mismatch와 cross-scope 접근·쓰기는 0이며 Workspace만으로 Notebook을 자동 선택하지 않는다. OpenAPI·Web·Desktop Adapter는 같은 exact 계약으로 동기화한다.

Phase E Review1 보완 계약은 Windows Native 7 operation의 canonical Notebook wire scope와 승인 Citation 8필드, Source/processing Repository SQL-level Binding prefilter, Web exact safe projection, BFCache pre-validation synchronous conceal, SELF_LOGOUT outbox startup recovery·immutability를 포함한다. disposable PostgreSQL test ID/count와 db0/role0 cleanup은 secret-free Evidence로 남긴다.

Phase E Review2 Critical은 production `main.jsx → DesktopShell`에 Notebook Home·선택 state가 없어 Adapter가 항상 unavailable인 누락을 닫는다. Native Session 뒤 서버 Notebook list/create/get/context를 사용하고, 사용자가 선택해 재검증한 Notebook만 3열에 전달한다. default/first/fixed ID 선택과 Browser BFF 호출은 0이며 history·deep-link·Workspace 전환·expiry/logout에서 재검증·fail-close한다.

`status | issue_id | 수행 작업 | 생성·변경 결과 | RED/GREEN 및 전체 검증 | actual PG/Windows/UI/Network 증거 | 보호 상태 | 미해결 | 다음 판단`

모든 actual Gate가 닫힌 경우에만 `COMPLETED`를 사용한다. 자동 테스트만 통과했거나 Groq·Upstage actual generation, Ollama installed-model connection, Windows·Sync Gate가 미실행이면 `INCOMPLETE` 또는 `BLOCKED`로 정직하게 분류한다.

Phase E 코드 독립 검토 후 남은 Windows actual blocker는 `R1-M8-10-WINDOWS-WEBVIEW-RECOVERY-I001`로 분리한다. 해당 복구 Work Order는 제품 기능 재작업이 아니라 Window/WebView 실행환경 원인 규명과 실제 Gate만 소유한다. 이 복구 결과가 PASS하기 전에는 전체 Work Order의 Windows actual 완료를 주장하지 않는다.

## 2026-08-14 Web 최종 화면 재인계 예외

| 항목 | 승인 내용 |
| --- | --- |
| Issue | `R1-M8-10-WEB-FINAL-UI-I001` |
| 사용자 승인 | 현재까지 구현된 실제 기능을 보존한 최종 Workspace 화면을 `daon-user.sinsan.kr`에서 검토할 수 있게 구현·배포 |
| Writer | 어울2 `daon-developer` 단일 Writer 재인계 |
| 허용 범위 | 승인 계획 Task 0·0A·0B의 Web React/CSS·관련 테스트·Progress/Evidence |
| 보호 범위 | Backend/API/DB/Migration/Desktop/Mobile/Local Service와 무관 dirty 전체 |
| 배포 | 어울1 검토·승인 후 어울1이 exact stage·commit·push 및 ysna Web-only 배포 수행 |

기존 `어울2 commit·push·merge·배포 금지`는 유지한다. 다만 세 번째 INCOMPLETE 뒤 기록된 제품/테스트 쓰기 중지는 이 새 Issue의 Web UI 범위에 한해 해제하며, 어울2는 이 범위의 구현과 테스트만 수행한다. 설계 §6의 Web 변경 제외는 공개 API·BFF·데이터 계약 변경 금지로 해석하고, 사용자 화면 교체는 본 예외로 허용한다.

완료 조건은 기본 Workspace, 보고서 설정, 생성 중·오류·완료, 저장 산출물 상세, 설정 Menu·LLM 설정 Popup의 실제 React 상태와 기존 기능 callback 보존, 1920×1080 실제 화면, build/type/boundary/same-origin 검증이다. 내부 URL·Credential·정책 코드 노출 또는 가짜 Provider 성공은 실패다.

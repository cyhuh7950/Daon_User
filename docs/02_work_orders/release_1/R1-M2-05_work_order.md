# R1-M2-05 작업지시서 — Studio 업무 흐름

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M2-05` |
| issue_id | `R1-M2-05-I001` |
| 작업 | 다섯 Studio 산출물의 생성 설정·편집·Version·검토·승인·Export·생산 지식 등록 Production-bound Prototype |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| 기준 Branch | `codex/r1-m2-05` |
| 기준 SHA | `d671e5cd173ed0c9630b45e739ce314793b5191a` |
| 선행 작업 | `R1-M2-02`, `R1-M2-04` 완료·Merge; `R1-M2-03` Source·권위 계약 재사용 |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M2-05_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-05_attempt-1.md` |

어울2는 착수 전에 아래 정본을 EOF까지 읽고 SHA-256을 대조한다. 요약본으로 대체하지 않는다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| `docs/04_test_reports/release_1_test_plan.md` | `C45DAE31FD408AF0D8885E006E570CC3BE36852A9F925811F8BC329C85ED9D13` |
| `docs/04_test_reports/release_1/scenarios/04_studio.md` | `197F598B24F5CEE1B08BF1457D2417F2AE4D7DBF69EBD5EEF969028FAE60BC14` |
| `docs/01_architecture/workspace_layout_state_adapter_contract.md` | `3E3A95C5299A2B68519A631DEC75CA03F71712B32DA1F43553CFCA434C2731C8` |
| `docs/01_architecture/source_authority_prototype_adapter_contract.md` | `F74E7E07DB9D93804C03DC3C887F5C570F3ACFAD6E1CCAFA3F7D44C29B59DA79` |
| `docs/01_architecture/run_model_evidence_prototype_adapter_contract.md` | `6D9D94C6B1734D93C4D50340C63216133C976C8661C12187637CF0F2AF492712` |

## 2. 목적과 사용자 완료 여정

사용자가 Studio에서 산출물 유형을 고른 뒤 즉시 생성되지 않고 정책 잠금이 반영된 최종 설정을 확인·확정하며, 초안부터 검토·승인·전달·명시적 생산 지식 등록까지의 수명주기를 한 흐름에서 이해할 수 있는 Production-bound Prototype을 완성한다.

최소 사용자 여정은 다음과 같다.

1. 다섯 Tile 중 산출물 유형을 선택한다.
2. 목적·독자·Source/SourceVersion·KnowledgeScope·RuleSet·분량/구성·출력 형식·전문가 검토 조건을 확인한다.
3. 강제 RuleSet·조직 검토·권위/가중치 Clamp·데이터 영역·외부 전송 정책의 잠금 사유와 완화 불가를 확인한다.
4. 설정을 확정해 불변 `GenerationSettingsSnapshot` Preview를 만들고 명시적으로 생성 제출한다.
5. 생성·사용자 편집·AI 재생성을 구분해 Revision과 불변 OutputVersion 계보를 본다.
6. 검토 요청→수정 요청 또는 승인→재제출 흐름과 ApprovalRequest 만료·회수를 확인한다.
7. 승인 후 변경이 새 OutputVersion과 재승인을 강제하고, 승인되지 않은 Version의 전달을 막는지 확인한다.
8. 승인된 특정 OutputVersion을 Export·전달하고, 별도 명시 요청으로만 생산 지식에 등록한다.
9. 모바일 화이트리스트 작업은 허용되고 구조·근거·설정·전체 재생성은 UI와 Gateway 계약 모두에서 차단되는지 확인한다.

## 3. 구현 범위

### 3.1 다섯 산출물과 생성 설정

- Tile은 `evidence_report`, `compliance_checklist`, `comparison_table`, `knowledge_map`, `business_draft` 다섯 유형을 제공한다.
- 유형별 필수 구성과 허용 출력 형식을 설계서 §13.1·§13.6과 정확히 연결한다.
- Tile 선택은 `GenerationRequest.configuring`으로만 전환하며 생성·성공을 시작하지 않는다.
- 설정은 목적, 대상 독자, Source/SourceVersion/KnowledgeScope, RuleSet Binding/Version, 분량·Section·표·도식·Template, 출력 형식, 전문가 검토 조건을 가진다.
- 기본값과 조직 Template은 출처를 표시한다. 강제 RuleSet과 조직 검토 조건은 잠금과 사유를 표시한다.
- 권위 우선순위·가중치 Clamp·강제 RuleSet·데이터 영역·Egress 정책은 완화할 수 없고 잠금 Control이 실제 변경을 거부한다.
- 필수값과 유형별 출력 형식이 유효해야 `confirmed`가 된다. `confirmed` 전에는 생성 제출을 허용하지 않는다.

### 3.2 GenerationSettingsSnapshot·제출 경계

- 확정 시 Actor·Workspace·산출물 유형·모든 설정값·잠금 정책 Version·SourceVersion·RuleSetVersion·가중치 유효값·검토 조건·확정 시각을 깊은 불변 Snapshot으로 만든다.
- `GenerationRequest: configuring → confirmed → submitted`를 순수 상태 전이로 표현한다.
- 제출 전 설정 변경은 확정을 무효화해 `configuring`으로 돌아가며 새 확정 전 제출할 수 없다. Output Revision은 0건이어야 한다.
- 제출 후 설정 변경은 기존 Request·Snapshot을 수정하지 않고 새 GenerationRequest·Revision·OutputVersion Preview를 만들며 변경값과 사유를 요구한다.
- Snapshot은 Run·StudioOutput·최초 OutputVersion과 불투명 ID로 연결한다. 실제 API·DB·파일 생성은 M8 책임으로 남긴다.

### 3.3 편집·Version·근거

- `StudioWorkflowViewState`를 Workspace 정본 상태에 연결하고 Pane 언마운트·폭 전환 뒤 선택 Tile, 설정 Draft, 열린 OutputVersion, 편집 Cursor, 검토 상태를 보존한다.
- `generation | user_edit | ai_regeneration` Revision 유형을 구분한다.
- 저장된 OutputVersion은 수정하지 않는다. 모든 변경은 `previous_version_id`와 변경 사유를 가진 새 Version으로 표현한다.
- 부분 재생성은 대상 Section과 Evidence/Run Trace를 연결하고 다른 Section 불변을 표시한다.
- Version 비교에서 변경 내용·사유·Revision 유형·근거 변경 여부를 확인할 수 있게 한다.
- Citation은 M2-03 Evidence Viewer를 Evidence ID로 열며 SourceVersion·위치·문맥 계보를 보존한다.
- 근거 부족·미확인·중요 충돌은 경고로 표시하고, 해결 전 승인·전달·생산 지식 등록을 차단한다.
- 각 OutputVersion은 §13.4 공통 계약 전체를 가진다: Output ID·유형·Owner·Workspace, GenerationRequest·GenerationSettingsSnapshot, Content·Format, Source·KnowledgeScope·EvidenceReference, 권위·가중치·RuleSet Snapshot, Provider·Model·Prompt·Tool 계보, 경고·미확인 사항·신뢰 상태, Revision 유형, 불변 Version·previous_version_id·변경 사유, 검토·승인·전달·생산 지식 등록 상태.

### 3.4 검토·승인·재승인

- `OutputVersion: generating → draft → review_requested → in_review → revision_requested → draft(새 Version)` 또는 `approved → delivered`를 결정론적으로 표현한다.
- `ApprovalRequest: pending → approved | rejected | expired | withdrawn`을 OutputVersion 상태와 분리한다.
- 반려는 대상 Version을 `revision_requested`로 전환하며 새 Draft Version을 만든다.
- 기본 만료 7일, 조직 허용 1~30일, 만료 24시간 전 알림, 판정 전 회수, 자동 승인 0건을 표시한다.
- `expired`·`withdrawn`은 ApprovalRequest만 종료하고 대상 OutputVersion과 Review·Approval·Audit 계보를 보존한다. 재요청 전까지 승인·전달 상태로 자동 전환하지 않는다.
- 다시 승인받을 때 기존 ApprovalRequest를 재사용하지 않고 새 요청을 만든다.
- 승인 후 내용·근거·가중치·모델·RuleSet·생성 설정 변경은 새 Version과 재승인 필요 상태를 만든다.
- 승인된 OutputVersion만 전달할 수 있다. `Run.waiting_approval`을 Output 승인 대기로 재사용하지 않는다.

### 3.5 Export·전달·생산 지식 등록

- 유형별 허용 파일 형식과 Export Preview를 제공하되 실제 파일 생성·다운로드 성공으로 위장하지 않고 `Prototype · unavailable`을 명시한다.
- Export Preview에는 OutputVersion, 생성 시각, KnowledgeScope, 허용된 근거 부록을 표시한다.
- Export·Delivery·KnowledgeRegistration은 승인된 OutputVersion, 해당 작업 권한, 현재 Membership·Workspace ACL·SourceVersion 권한·조직 정책을 기준으로 `AccessDecision=available | partially_redacted | access_blocked`를 매 요청마다 각각 재검증한다. 미승인·무권한·다운로드 금지·`access_blocked` 상태는 `CURRENT_ACCESS_DENIED` 등 안정적 안전 Code와 함께 차단하고, `partially_redacted`는 허용 범위와 마스킹된 Reference를 분리 표시한다.
- Delivery는 승인 Version·대상·현재 접근 판정·Audit Preview를 요구하며 미승인·중요 충돌 상태를 차단한다.
- 산출물은 승인·전달만으로 자동 Source가 되지 않는다.
- 권한 있는 사용자가 특정 불변 OutputVersion에 대해 별도 `KnowledgeRegistration`을 명시 요청해야 `requested → registered | rejected`로 전환한다.
- Editor·Reviewer·Approver·Viewer Fixture로 승인, Export/Download, Delivery, KnowledgeRegistration의 허용·거부 Matrix를 표시한다. M2-06 이전에는 실제 403을 주장하지 않고 안정적 Prototype 안전 Code와 후속 책임을 명시한다.
- 등록된 생산 지식은 원본 자료·Run·모델·편집자·검토자·Version 계보를 표시한다. 원본 산출물 후속 편집이 등록 Version을 바꾸지 않는다.
- 순환·동일 내용 파생 감지와 Daon 쓰기 0건을 계약으로 표시한다. Release 1에서 Daon 자동 승격·쓰기 호출은 금지한다.

### 3.6 모바일 화이트리스트

- 허용: 제목, 기존 Text Block 인라인, 기존 단순 표 Cell 값, Review Comment, 수정 요청, 승인·반려, 알림 처리, Citation/Evidence 열람.
- 차단: Section/Page/Layout 구조, 행·열·병합·수식·서식 구조, Citation/Evidence 연결, 생성 설정, 전체 재생성.
- 제목·기존 Text Block·단순 표 Cell의 내용 변경만 `user_edit` Revision과 새 OutputVersion을 만든다.
- Review Comment·수정 요청은 Review/Audit 상태, 승인·반려는 Approval/Audit 상태, 알림 처리는 Notification 상태, Citation/Evidence 열람은 Read/Audit 상태로 분리하며 Content Revision을 만들지 않는다.
- 차단 작업은 Revision·Version을 만들지 않고 명시 안전 오류와 Web·Windows 이어서 작업 안내를 제공한다.
- UI 숨김만으로 끝내지 말고 향후 Native Gateway가 소비할 순수 Allowlist 판정과 안정적 안전 Code를 구현한다. M3는 Client Shell 연결, M4는 Gateway/API 강제 계약, M8은 Android·iOS 실제 Studio 흐름 검증을 소유한다.

### 3.7 Prototype 정직성·접근성·반응형

- 실행되지 않은 API·DB·LLM·파일 Export·전달·지식 Index를 완료로 표시하지 않는다.
- 실제 Runtime이 없는 Control에는 `Prototype · unavailable`과 후속 구현 Milestone을 표시한다.
- 1920×1080 기준, 본문·폼 12px, 작은 설명 10px, 아주 작은 보조 9px, 사이드바 14px, 제목 16px을 유지한다.
- 상시 설명 박스를 추가하지 않고 `i` 아이콘·Tooltip·Popover를 사용한다.
- Keyboard·Focus·ARIA·비색상 상태를 제공하고 1440+, 1024~1439, 600~1023, 599- 전환에서 업무 상태를 보존한다.
- Browser 코드는 same-origin 상대 경계만 유지하고 API 절대주소·localhost·Docker Host/Port·`NEXT_PUBLIC_API_BASE_URL` 직접 호출을 금지한다.

## 4. Production-bound 재사용·교체 계약

- M2-02 Workspace Layout·State·Evidence Viewer, M2-03 Source/Authority Snapshot, M2-04 RunSnapshot·Evidence 계보를 수정 재정의하지 말고 직접 재사용한다.
- 순수 Studio Domain Model·Reducer·Allowlist·표시 Component는 M8의 실제 API Adapter가 승계 가능한 형태로 분리한다.
- Prototype Fixture와 실제 Adapter 경계를 문서화하고, M3 Client Shell과 M8 Studio 구현에서 재사용할 파일·교체할 Fixture·금지된 임시 경로를 표로 남긴다.
- 공개 API·데이터 계약·보안 경계를 새로 확정하지 않는다. 필요하면 구현을 중지하고 어울1에게 회부한다.

## 5. 허용 변경 범위

- `packages/ui/src/`의 Studio 전용 Domain Model·Pane·기존 Workspace 연결 최소 수정
- `scripts/tests/`의 Studio 전용 Test와 기존 Workspace 회귀 Test 최소 수정
- `docs/01_architecture/studio_workflow_prototype_adapter_contract.md`
- `docs/03_evidence/release_1/R1-M2-05/`
- 지정 진행 기록과 결과보고

금지:

- API·DB·Migration·Lockfile·Dependency·Toolchain·CI 설정 변경
- M2-02~04 정본 계약의 재작성, 무관 Refactor, 기존 Fixture 삭제
- 실제 파일·LLM·Provider·지식 등록 성공 위장
- 보호 Dirty `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`, `violations.json` 수정·복원·Stage

## 6. TDD와 구현 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| S0 | 정본·Hash·선행 계약·Branch·단일 Writer·보호 Dirty 확인 | 진행 기록 |
| S1 | 생성 설정·Snapshot·수명주기·재승인·등록·모바일 Allowlist Test를 먼저 작성 | 계약별 유효 RED |
| S2 | 순수 Studio Domain Model·Reducer 최소 구현 | 전용 Model Test PASS |
| S3 | Studio Pane과 Workspace 상태 연결 | 기존 Workspace·Source·Run 회귀 PASS |
| S4 | 다섯 Tile·설정 잠금·Snapshot Preview·Version 비교 | UI 계약 Test PASS |
| S5 | 검토·승인·만료·회수·재승인·전달·등록 분기 | 상태 Matrix PASS |
| S6 | 모바일 허용·차단 Matrix·접근성·반응형 | 네 폭·Keyboard 계약 PASS |
| S7 | Production Build와 실제 Browser 클릭 검증 | Console·Network·시각 증거 |
| S8 | Architecture·Manifest·Diff·결과보고 | HANDOFF_READY |
| S9 | 어울1 Commit·Push 후 GitHub·ysna-server 불변 SHA 검증 | Required Check·Artifact·ARM64 PASS |
| S10 | 읽기 전용 독립 검토 | ACCEPT 또는 REWORK |
| S11 | 어울1 최종 대조와 Merge | Merge SHA |

각 기능은 실패 Test→최소 구현→회귀 순서로 진행한다. 환경·Loader 오류는 유효 RED로 계산하지 않는다.

## 7. 자동·Browser 검증

### 7.1 자동 검증

- 다섯 Tile과 유형별 필수 구성·허용 형식
- 설정 필수값·잠금·확정·제출 전/후 변경
- Snapshot 깊은 불변성과 Request/Run/Output 연결
- Revision 유형, OutputVersion 불변, previous Version·변경 사유, §13.4 Output 공통 계약 전체 필드
- ApprovalRequest 정상·반려·만료·회수, 만료·회수 뒤 OutputVersion·Audit 보존과 재승인
- 미승인·중요 충돌·무권한·현재 접근 차단의 Export·Download·전달·등록 차단
- 자동 생산 지식 등록·Daon 쓰기 경로 0건
- 모바일 Content 변경 Revision과 Review·Approval·Notification·Read 상태 분리, 허용·차단 작업 Matrix와 안전 Code
- Workspace 상태 보존·Evidence Viewer 연결·금지 URL 0건
- Prototype UI·상태·정적 Source에서 내부 Chain-of-Thought 원문 노출 0건. 실행 단계·근거·규칙·결과만 표시
- 기존 Foundation·Workspace·Source·Run 전수 회귀, Lint, Build, 공통 Gate

### 7.2 실제 Production Browser

새 Production Build·새 Browser 세션에서 다음 상태를 실제 클릭하고 1920×1080, 1200×900, 800×900, 500×900 증거를 남긴다.

- 다섯 Tile과 생성 설정 전체 필드
- 강제 RuleSet·조직 검토 잠금 및 사유
- 설정 확정→변경 무효화→재확정→제출
- Draft 편집→Version 비교→검토 요청→수정 요청→재제출→승인
- 승인 후 변경→새 Version·재승인 강제
- 미승인 전달 차단, 승인 Version Export/Delivery Preview
- 명시 KnowledgeRegistration과 자동 등록 부재
- 모바일 허용 작업과 차단 작업·연속 작업 안내
- Console Warning/Error, 실제 Resource Timing 가용 여부, API-like 요청·비동일 Origin·금지 주소를 구분해 기록

Resource Timing API가 도구 문맥에서 제공되지 않으면 0으로 쓰지 않고 `unavailable`과 사유를 기록한다. 정적 Asset/Document Resource를 API 요청으로 오인하지 않는다.

### 7.3 M2 Fixture와 후속 실제 검증 추적표

| 테스트 범위 | 이번 M2-05에서 검증 | 실제 완료 책임·이번 금지 주장 |
| --- | --- | --- |
| TS-STU-001~006 | 실행 계획·비동기·중복·취소·추론 비노출·대화 연결의 상태/화면 Fixture와 M2-04 Run 계약 재사용 | M4/M6/M8 실제 Event·Idempotency·API·LLM; 이번에 실제 실행 성공 주장 금지 |
| TS-STU-007~009B | 생성 설정·잠금·확정·제출 전후 상태와 불변 Snapshot 순수 전이 | M8 실제 저장·API·Run 연결 |
| TS-STU-010~016 | 다섯 유형의 필수 구성·허용 형식·Export Preview, 근거 부족 상태, §13.4 공통 계약 필드 Fixture | M8 실제 DOCX·PDF·XLSX·CSV·JSON·SVG·PNG 생성·응용프로그램 Open·Layout·원문 대조 |
| TS-STU-020~023 | Revision/Version 불변·부분 재생성 계보·비교 화면 Fixture | M8 DB 불변성·실제 재생성·API 수정 거부 |
| TS-STU-030~032·034~035 | Review·ApprovalRequest·만료·회수·재승인 상태 Matrix | M4 권한/API·알림/Audit 계약, M8 실제 계정·시간 경과·전체 승인 흐름 |
| TS-STU-033 | 역할별 Control과 Prototype 안전 Code Matrix | M2-06 권한 Prototype, M4 권한/API 403·정보 비노출, M8 실제 역할 계정 흐름 |
| TS-STU-040~043 | Delivery·Export/Download Gate·현재 AccessDecision·부록 Preview | M4 AccessDecision·Notification·Audit 계약, M5 파일 저장, M8 실제 Delivery·Export/Download |
| TS-STU-050~056 | 명시 등록·자동 등록 부재·불변 계보·순환 표시·Daon 쓰기 0 Fixture | M6/M8 실제 SourceVersion·Index·권한·Connector Network 0 |
| TS-STU-060~062 | 모바일 허용/차단 순수 Allowlist, Content Revision과 비Content 상태 분리, 이어서 작업 안내 | M3 Client Shell, M4 Native Gateway/API 강제 계약, M8 APK/Archive·실기기·실제 서버 거부 |

이번 결과보고와 Browser JSON은 각 검증을 `prototype_fixture` 또는 `deferred_actual`로 표시한다. `deferred_actual` 항목은 PASS 수에 포함하지 않는다.

## 8. 완료 조건

- §2 사용자 여정과 §3 계약을 클릭 가능한 Prototype으로 충족
- 다섯 산출물 유형, 생성 설정 7개 범주, 정책 잠금, Snapshot, §13.4 Output 공통 계약, 전체 수명주기, 재승인, 권한/현재 접근 Gate, 등록, 모바일 Matrix 누락 0건
- 기존 M2-02~04 기능과 상태 보존 회귀 0건
- 전용·Workspace·Source·Run·Foundation Test, Lint, Production Build, 공통 Gate PASS
- 실제 Browser 네 폭에서 Console 오류 0건, 화면 잘림·겹침·상태 초기화 0건
- 금지 URL·Raw Provider·Secret·Daon 쓰기·자동 등록 경로 0건
- Architecture 계약, Browser JSON, Screenshot, Evidence Manifest, 진행 기록, 결과보고 완비

실제 파일 Open·Studio API/DB·Delivery·Index는 M8에서 검증한다. Provider/LLM 실행은 선행 M4~M6 계약과 M8 Studio E2E에서 대조하며, 이번 Prototype 완료로 주장하지 않는다.

## 9. ysna-server 경계

S9 서버 검증은 `/home/ubuntu/deploy/daon-user/R1-M2-05/<exact-push-sha>` 격리 Checkout으로 제한한다. 기존 `shared-db`, `common`, `netdata`, `proxy`를 사용하거나 변경하지 않는다. 기존 Container·Network·Volume의 사전·사후 Hash 일치, detached exact SHA·Clean 상태, ARM64 Build·Test·Gate, 임시 자원 0을 증거로 남긴다. Schema·Migration 신호가 없으면 `NOT_APPLICABLE_NO_SCHEMA`와 DB 명령 0건을 기록하고 임의 DB를 만들지 않는다.

## 10. 진행 기록·결과보고

어울2는 착수, 각 세부 단계 완료, 오류·복구, Test·Build·Browser 완료, 종료 직전에 진행 파일에 시각·단계·상태·변경 파일·명령/Exit·검사 결과·오류/원인·복구·증거·남은 위험·다음 작업을 기록한다.

결과보고 첫 줄은 다음 형식으로 고정한다.

```text
COMPLETED | R1-M2-05-I001 | 요약 | 변경 파일 | 테스트 근거 | 남은 위험 | 다음 조치
```

완료 조건이 하나라도 없으면 `COMPLETED`를 사용하지 않는다. 첫 오류만으로 `FAILURE_REPORT`를 제출하지 말고 원인·대안·현재 변경·검증 근거를 먼저 정리한다. 승인 경계 변경이 필요하면 쓰기를 중지하고 어울1에게 보고한다.

## 11. 판정 기준

- `COMPLETED`: 필수 산출물·테스트·Browser 증거·보고가 모두 있고 범위 위반이 없다.
- `FAILURE_REPORT`: 원인과 대안을 조사했지만 승인 경계 안에서 완료할 수 없음을 정식 보고한다.
- `INCOMPLETE`: 응답 종료·시간 제한 등으로 필수 산출물 또는 증거가 빠졌다.

중대 미진은 별도 수정 작업지시서로 재작업한다. 합격 가능한 경미 보완은 다음 작업지시서에 흡수하며 사소한 사유로 전체 합격 작업을 다시 열지 않는다. 검토 출력은 `판정 → 판단 이유 → 조치` 순서로 한다.

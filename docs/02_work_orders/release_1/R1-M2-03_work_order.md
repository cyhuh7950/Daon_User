# 작업지시서 R1-M2-03 · Source·지식·권위 흐름

## 0. 문서 정보

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M2-03` |
| issue_id | `R1-M2-03-I001` |
| Version | `1.0` |
| 작성일 | `2026-07-21` |
| 작성·기술 판단 | 어울1 |
| 실행 | 어울2 · `daon-developer` |
| 기준 Branch | `codex/r1-m2-03` |
| 기준 Commit | `6bbd55d402d8a255b640b47e67a861ba896bc923` |
| 선행 Work Order | `R1-M2-02` · `COMPLETED` |

## 1. 승인 정본

다음 문서를 요약본으로 대체하지 말고 EOF까지 읽은 뒤 수행한다.

| 정본 | 경로 | SHA-256 |
| --- | --- | --- |
| 상세 설계서 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| Release 1 테스트 계획 | `docs/04_test_reports/release_1_test_plan.md` | `C45DAE31FD408AF0D8885E006E570CC3BE36852A9F925811F8BC329C85ED9D13` |
| M2-02 결과보고 | `docs/02_work_orders/reports/R1-M2-02_attempt-3.md` | `6A89526A54B56B40D5C93CA5085E583C6B01DCBB8F5E5E0F41B3BAA31627FC0D` |
| Workspace 승계 계약 | `docs/01_architecture/workspace_layout_state_adapter_contract.md` | `3E3A95C5299A2B68519A631DEC75CA03F71712B32DA1F43553CFCA434C2731C8` |

우선 적용 조항은 상세 설계 §6~8.2, §16, §18.1, §24, §27, 불변조건 INV-14~15, 결정 R1-D013·014·018·019와 Release 1 계획 §4.3 `R1-M2-03`이다. M2-02의 Layout·상태 보존·접근성 공개 계약은 변경 없이 소비한다.

## 2. 단일 목표와 완료 모습

### 단일 목표

자료·지식 Pane에서 Source 등록부터 Modality별 처리, 불변 Version, 지식 유형·권위·가중치, RuleSet 잠금과 중요 충돌 검토 차단까지를 실제 Browser에서 클릭 가능한 Production-bound Prototype으로 구현한다.

### 사용자 관점 완료 모습

- 다섯 지식 원천을 구분해 보고 Source별 처리 상태·Version·권위·실제 적용 가중치를 확인한다.
- 가중치는 `0.5~2.0`, `0.1` 단위, 기본 `1.0`이며 개별 Source→그룹→유형→기본값 중 가장 구체적인 하나가 적용됨을 화면에서 확인한다.
- Clamp가 일어나면 요청값·적용값·적용 계층·사유를 확인하고, 가중치가 권위를 뒤집지 못함을 본다.
- 강제 RuleSet은 잠겨 해제할 수 없고, 선택형 RuleSet은 조건·Version·실패 방식과 함께 구분된다.
- 중요 충돌은 자동 판정 근거와 관련 Source를 보여주고 해결 전 승인·외부 전달·생산 지식 등록을 차단한다.
- 문서·표·이미지는 Vision/LLM-first, 오디오는 직접 Audio LLM 또는 ASR+LLM 의미 이해 두 경로를 보이며 Parser/OCR-only·ASR-only 성공은 없다.
- 정상·`waiting_model`·`partial_understanding`·`needs_review`·정책 차단·`failed`·`expired` 상태를 클릭해 원인과 다음 행동을 확인한다.
- 실제 Upload·API·DB·LLM 실행은 아직 연결하지 않고 `프로토타입 데이터`/`unavailable`로 명시한다.

## 3. 포함 범위

### 3.1 Production-bound 책임 경계

- M2-02 `AdaptiveWorkspace`와 단일 `WorkspaceViewState`를 그대로 사용하고 자료·지식 Pane의 Domain View·상태 Adapter만 확장한다.
- `packages/ui`는 M3가 승계할 Source·Authority 표현 Component와 순수 상태 전이·검증 모델을 소유한다.
- `apps/web`은 실제 Browser에서 흐름을 클릭하는 Prototype Harness를 소유한다.
- 실제 파일 전송, 보안 검사, 저장, API/BFF, DB, Queue, 모델 호출, Search·Index는 구현하지 않는다.
- Prototype 동작은 Network 요청을 만들지 않고, 연결되지 않은 실행은 성공으로 위장하지 않는다.

### 3.2 Source 등록·Version·계보

- 등록 진입에서 파일·직접 입력·인터넷·LLM 일반지식·Daon 승인 지식·사용자 생산 지식의 화면 분류를 제공한다. RuleSet은 검색 Source와 분리한다.
- Source Detail은 ID·유형·소유/Workspace·데이터 영역·민감도·원본 Digest·불변 Version·출처·조회 시각·처리/색인/검토 상태를 표시한다.
- Version 선택과 이전 Version 열람은 가능하되 과거 Version을 덮어쓰지 않는다.
- 사용자 생산 지식은 `명시적 등록 필요` 상태만 표현하고 자동 순환·Daon 승인 지식 자동 승격을 만들지 않는다.
- Source 등록 버튼은 Prototype 흐름만 열며 실제 Upload 완료나 `ready`를 만들지 않는다.

### 3.3 Modality별 처리 흐름

| Modality | 정상 단계 | Ready 금지 조건 |
| --- | --- | --- |
| 문서·표·이미지 | `vision_llm_understanding → parser_ocr_validation → evidence_reconciliation → indexing → ready` | Parser/OCR-only |
| 오디오 직접 이해 | `audio_llm_understanding → transcript_timecode_validation → evidence_reconciliation → indexing → ready` | 의미 이해·시간 검증 누락 |
| ASR+LLM 오디오 | `speech_to_text → llm_semantic_understanding → transcript_timecode_validation → evidence_reconciliation → indexing → ready` | ASR-only |

- 각 단계는 현재 단계, 완료 단계, 대기/분기 이유와 Evidence 위치(Page·Cell·Region·시간 구간)를 표시한다.
- `waiting_model`은 필요한 역할과 Runtime 부재를 표시하되 자동·수동 새 Run 실행은 M2-07 소유이므로 이번에는 동작하지 않는 명시적 진입 자리만 둔다.
- 정책 후보 0개는 ProcessingRun `policy_blocked`·Source `needs_review`, Runtime 후보 소진은 `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`·Source `waiting_model`로 구분한다.
- `partial_understanding`은 성공/누락 범위와 기본 검색·생성 제외를 표시하고 `재처리 요청`, `검토 요청`, `사용 중지` 선택 자리를 제공한다. 실제 Run 생성은 `unavailable`이다.
- Parser/OCR 또는 의미 이해 불일치는 양쪽 결과와 위치를 보존하고 `needs_review`로 전환한다.
- `failed`, `expired`는 원인·복구 가능성·재등록/재처리 진입을 표시하되 묵시적으로 `ready`로 전환하지 않는다.

### 3.4 권위·가중치·RuleSet

- 권위 순서는 강제 RuleSet 조건 → Daon 승인 지식 → 사용자 파일·직접 입력·생산 지식 → 출처 확인 인터넷 → LLM 일반지식이다.
- 강제 RuleSet은 점수 경쟁 Source가 아니며 잠금·적용 조건·Version을 표시한다.
- Daon 승인 지식의 최고 근거 권위·기본 Boost·최소 포함 슬롯을 표시하되 서로 다른 Tier를 단일 곱셈 점수로 경쟁시키지 않는다.
- 가중치 Control은 `0.5~2.0`, Step `0.1`, 기본 `1.0`을 강제한다. 유형·그룹·개별값을 함께 구성해 가장 구체적인 값 하나만 적용하고 곱하지 않는다.
- 조직 정책 범위를 벗어난 요청은 Clamp하며 요청값·적용값·적용 계층·Clamp 사유를 Snapshot Preview에 보여준다.
- Source 제외는 가중치 `0`이 아니라 별도 활성화 상태로 표현한다.
- 잠긴 값과 RuleSet의 이유는 `i` 아이콘 Tooltip/Popover로 제공하되 잠금·오류·차단 핵심 상태는 화면에 보이게 한다.

### 3.5 충돌·검토 차단

- `ConflictRecord.severity`의 `informational | material | critical`을 모두 표현한다.
- `material`·`critical`은 중요 충돌이며 자동 `ConflictPolicyVersion` 판정 근거, 관련 Source/문장/Version, 적용·배제 사유를 표시한다.
- 검토자는 심각도를 올릴 수 있지만 정책으로 잠긴 중요도를 낮추는 UI를 제공하지 않는다.
- 미해결 중요 충돌에서는 승인·외부 전달·생산 지식 등록을 비활성화하고 `review_required=true`와 차단 이유를 표시한다.
- 중요하지 않은 차이도 숨기지 않고 informational 충돌·대안으로 공개한다.
- 충돌 해결 동작은 Prototype 상태 전이로만 수행하고 Audit Preview에 판정 Version·검토자 행동을 남긴다.

### 3.6 적응형·접근성

- 1920×1080의 3면 Layout 안에서 자료·지식 흐름을 완성하고 기존 Resize·Pane·Drawer·Bottom Tab·Viewer 동작을 보존한다.
- `1200`, `800`, `500` 대표 폭에서도 Source List→Detail→상태/권위/충돌 흐름을 실제 클릭할 수 있다.
- 기본 12px·제목 16px, Token 직접 소비, Touch 44px 우선, Icon-only Accessible Name과 Tooltip/Popover 계약을 지킨다.
- Dialog/Drawer는 최초 의미 Control Focus, Tab 순환, Escape 닫기, Trigger Focus 복원과 배경 차단을 유지한다.
- Color만으로 권위·상태·충돌을 구분하지 않는다.

## 4. 권장 산출물 경계

실제 구조와 충돌하면 증거를 제출하고 어울1 판단을 받는다. 승인 없이 새 Package·외부 Runtime Dependency·공개 API를 추가하지 않는다.

- `packages/ui/src/`: Source·Authority·Weight·RuleSet·Conflict Component와 순수 상태 모델
- `apps/web/app/`: 기존 Workspace Route의 Prototype Seed/Harness 최소 확장
- `scripts/tests/`: 상태 전이·가중치·권위·충돌·접근성·금지 주소 계약 Test
- `docs/01_architecture/`: M3 승계 Source/Authority Prototype Adapter 계약
- `docs/03_evidence/release_1/R1-M2-03/`: Browser JSON·Screenshot·Manifest와 후속 GitHub/서버 증거
- `quality-gate-policy.json`: 새 Source에 필요한 기존 Capability 명령만 최소 연결

기존 Lockfile의 승인 의존성만 사용한다. 새 Dependency 설치·버전·Lockfile 변경은 금지한다.

## 5. 제외 범위

- 실제 Upload·Malware Scan·Storage·DB Schema·Migration·Index·검색
- 실제 모델 Routing·Fallback·비용 한도·Citation 조정(M2-04)
- Studio 생성 설정·산출물 수명주기(M2-05)
- 계정·조직·정책·장치 관리(M2-06)
- `waiting_model` 자동 Readiness Event·수동 재처리 Queue 실행과 운영 복구(M2-07)
- Backend/API/BFF/Auth/Provider/Secret과 실제 Daon Connector
- M3 플랫폼 Shell, M6 실제 Source Pipeline 완료 선언

## 6. 기존 기능과 불변조건

- M2-01 Route·Screen·Token·접근성 정본과 M2-02 Layout·상태 보존·Modal·Focus·Lint 계약을 바꾸지 않는다.
- 기존 Workspace Test 14건, Foundation 8건, Gate Test 25건, 독립성·7범주 Gate를 유지한다.
- Daon 승인 지식·RuleSet 우선, Vision/LLM-first, Parser/OCR 보조, ASR-only ready 금지를 문구와 상태에서 뒤집지 않는다.
- Browser 코드에서 API 절대주소, `localhost`, `127.0.0.1`, Docker 내부 주소/Port, `NEXT_PUBLIC_API_BASE_URL`을 금지한다.
- 기존 R1-M1-04 Dirty 표시 두 파일과 `shared-db`, `common`, `netdata`, `proxy`를 수정·복원·Stage하지 않는다.
- 요구되지 않은 Refactor·전체 재작성·설정 변경을 금지한다.

## 7. 실행 단계

| 단계 | 작업 | 완료조건 |
| --- | --- | --- |
| S0 | 정본·Hash·기준 SHA·Diff·M2-02 승계 계약 확인, 진행 기록 착수 | 보호 파일·변경 범위·단독 Writer 확인 |
| S1 | 실패하는 Source 상태·가중치·권위·충돌·접근성 Test 작성 | 승인 계약 누락이 Red로 재현됨 |
| S2 | Source/Version/처리 상태 순수 모델과 Prototype Seed 구현 | 정상·분기 상태와 Ready 금지 계약 PASS |
| S3 | 지식 유형·권위·가중치·Clamp·RuleSet 잠금 구현 | 0.5~2.0·계층·권위 불변 PASS |
| S4 | 중요 충돌 판정·검토·최종화 차단 구현 | informational/material/critical과 차단 PASS |
| S5 | 기존 자료·지식 Pane에 등록·List·Detail·상태·충돌 흐름 연결 | Desktop 실제 클릭 가능, 성공 위장 0 |
| S6 | 적응형·Keyboard·Focus·Tooltip/Popover·상태 표현 완성 | 1920/1200/800/500 흐름과 접근성 PASS |
| S7 | Test·Lint·Build·품질 Gate와 M2-02 회귀 | 새 Dependency 없이 전체 Gate PASS |
| S8 | 실제 Browser 네 폭 클릭·Console/Network 증거 | 상태·가중치·충돌·오디오 두 경로, Console/API 오류 0 |
| S9 | 로컬 전체 회귀·Diff·Evidence Hash 후 Hand-off | `HANDOFF_READY`, 어울1 Commit·Push 대기 |
| S10 | 불변 SHA GitHub CI·ysna-server ARM64 검증 | Build·Gate·Migration N/A·자원 불변·Artifact PASS |
| S11 | Evidence·결과보고 | 완료조건 전수 대조와 정식 상태 제출 |

S9 이후 구현 코드를 수정하지 않는다. 어울1이 Diff를 검토해 Commit·Push한 불변 SHA를 전달한 뒤 같은 어울2가 S10부터 재개한다.

## 8. 테스트와 증거

### 필수 자동 검증

- 다섯 지식 원천과 RuleSet 별도 타입, 권위 순서 고정
- 가중치 범위·Step·기본값·개별>그룹>유형>기본·비곱셈·Clamp Snapshot
- 강제 RuleSet 잠금과 선택형 실패 방식 구분
- 문서 Vision/LLM-first, 오디오 직접/ASR+LLM 두 경로, Parser/OCR-only·ASR-only ready 0건
- `ready | waiting_model | partial_understanding | needs_review | failed | expired`와 ProcessingRun 정책 차단 구분
- 중요 충돌 자동 심각도·상향만 허용·미해결 최종화 3종 차단
- Source Version 불변과 생산 지식 명시 등록, 자동 승격·순환 0
- M2-02 상태·Layout·Modal·Focus·Home/Workspace Route 전수 회귀
- Browser 금지 URL·환경변수·API 요청 0
- Next Production Build·Typecheck·Lint·신규 Test·기존 전체 Gate PASS
- `git diff --check`, 추적 삭제·Lockfile·허용 범위 밖 변경 0

### 실제 Browser 증거

- 1920×1080과 1200/800/500 대표 폭 Screenshot
- Source 등록 진입→List→Detail→Version→처리 단계→권위/가중치→충돌 검토 실제 클릭
- 문서 정상, 오디오 직접, 오디오 ASR+LLM과 모든 분기 상태 선택 증거
- 가중치 계층 선택·Clamp와 강제 RuleSet 잠금 실제 조작
- 중요 충돌 차단 전/후 상태와 Focus·Keyboard·Tooltip/Popover 검증
- 기본 12px·제목 16px·Touch Target, Console Error 0, API/비동일 Origin/금지 주소 0

### 서버·GitHub 증거

- 정확 Push SHA, Clean Checkout, ARM64 Build·Test
- Schema가 없으면 `NOT_APPLICABLE_NO_SCHEMA`, DB 명령 0
- ysna-server 기존 Container·Network·Volume 사전·사후 Hash 불변과 임시 자원 0
- PR Required Check·Branch Protection·Artifact PASS·Exit 0·7범주·Failures 0

## 9. 진행 복구 기록

진행 파일은 `docs/04_test_reports/release_1/R1-M2-03_progress.md`다.

어울2는 착수, 각 세부 단계 완료, 오류·원인·복구, Test 완료, Browser 검증, Hand-off와 종료 직전에 다음을 즉시 기록한다.

`시각 | 단계 | 상태 | 변경 파일 | 명령·Exit | 검사 결과 | 오류·원인 | 복구·대안 | 증거 경로 | 남은 위험 | next_action`

장시간 설치·Build·서버·Browser 명령은 충분히 기다린다. 동일 명령을 근거 없이 반복하지 않고, 예기치 않은 중단은 실패보고로 집계하지 않으며 마지막 성공 단계부터 이어간다.

## 10. 결과보고 계약

결과보고 경로는 `docs/02_work_orders/reports/R1-M2-03_attempt-1.md`다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | Browser 증거 | 미해결 사항 | 다음으로 필요한 판단`

검토 출력은 `판정 → 판단 이유 → 조치` 순서로 작성한다. 중대 미진은 별도 수정 작업지시서로 회부하고, 합격 가능한 경미 보완은 다음 작업지시서에 흡수한다. 사소한 이유로 합격 작업 전체를 다시 열지 않는다.

## 11. 승인 경계

- 위 Source·권위·가중치·RuleSet·충돌 Prototype 계약 안의 구현 방법은 어울1 판단 범위다.
- 기능 범위·요구사항·공개 API·데이터 계약·보안 경계·중요 위험을 바꾸려면 쓰기를 중지하고 신산님 승인을 요청한다.
- Commit·Push·PR·Branch Protection 변경은 어울1이 수행한다.
- 외부 운영 배포·파괴적 작업은 이번 범위가 아니다.

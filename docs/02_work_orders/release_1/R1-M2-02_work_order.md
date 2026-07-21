# 작업지시서 R1-M2-02 · 적응형 3면 Workspace

## 0. 문서 정보

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M2-02` |
| issue_id | `R1-M2-02-I001` |
| Version | `1.0` |
| 작성일 | `2026-07-21` |
| 작성·기술 판단 | 어울1 |
| 실행 | 어울2 · `daon-developer` |
| 기준 Branch | `codex/r1-m2-02` |
| 기준 Commit | `863261a0ecb506816e57c0922ec2f7d5c1eb142a` |
| 선행 Work Order | `R1-M2-01` · `COMPLETED` |

## 1. 승인 정본

다음 문서를 요약본으로 대체하지 말고 EOF까지 읽은 뒤 수행한다.

| 정본 | 경로 | SHA-256 |
| --- | --- | --- |
| 상세 설계서 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| Release 1 테스트 계획 | `docs/04_test_reports/release_1_test_plan.md` | `C45DAE31FD408AF0D8885E006E570CC3BE36852A9F925811F8BC329C85ED9D13` |
| M2-01 결과보고 | `docs/02_work_orders/reports/R1-M2-01_attempt-1.md` | `729EB85C03C6D4369986A5BE2F827B63C1F8600471E6285503F35CFD451409DD` |

우선 적용 조항은 상세 설계 §5.2~5.3.1, §24, §27과 Release 1 계획 §4.3의 `R1-M2-02`다. M2-01의 Route·Screen·Token·접근성 정본을 변경 없이 소비한다.

## 2. 단일 목표와 완료 모습

### 단일 목표

자료·지식, 대화·실행, 업무 Studio의 세 작업 면과 근거 Viewer를 실제 Browser에서 클릭 가능한 Production-bound 적응형 Workspace로 구현한다.

### 사용자 관점 완료 모습

- `1440px+`에서는 세 면을 동시에 보고 각 면의 크기를 조절할 수 있다.
- `1024~1439px`에서는 현재 작업 두 면과 나머지 한 면 Drawer를 사용한다.
- `600~1023px`에서는 현재 작업 한 면과 보조 Drawer를 사용한다.
- `599px-`에서는 자료·대화·Studio 하단 Tab으로 전환한다.
- 화면 폭을 오가더라도 선택 Source, 대화 문맥, 실행 진행, 열린 산출물·편집 위치, 근거 Viewer 위치가 보존된다.
- 아직 연결되지 않은 Backend 기능은 `unavailable` 또는 명시된 Mock Adapter로 보이며 성공으로 위장하지 않는다.

## 3. 포함 범위

### 3.1 Production-bound 책임 경계

- `packages/ui`는 M3가 승계할 React 표현 Component, 적응형 Layout 상태 모델과 CSS를 소유한다.
- `apps/web`은 M2 Browser Prototype의 실제 실행·클릭 Harness를 소유한다. 이 Harness는 M3 Web Shell에서 같은 Component와 상태 모델을 소비한다.
- Domain 실행, API, DB, 인증, LLM, 검색, 파일 Upload와 실제 산출물 생성은 구현하지 않는다.
- Browser Source는 same-origin 상대 경로 외의 API 주소를 갖지 않는다. 이번 Work Order는 Network 요청 자체를 만들지 않는 것을 기본으로 한다.

### 3.2 고정 Layout 계약

| 폭 | layout_mode | 표시 계약 |
| --- | --- | --- |
| `>=1440` | `three-pane` | `knowledge`, `conversation`, `studio` 동시 표시, 근거는 보조 Drawer 또는 Overlay |
| `1024~1439` | `two-pane` | 사용자 선택 `primary`+`secondary` 두 면, 나머지 면은 명시 Drawer |
| `600~1023` | `single-pane` | 활성 면 하나, 다른 면과 근거는 Drawer |
| `<=599` | `bottom-tabs` | 활성 면 하나와 하단 3 Tab, 근거는 전체 화면 Viewer |

- 경계값 `599/600/1023/1024/1439/1440`을 자동 Test로 고정한다.
- 1920×1080에서 정보 밀도와 주요 작업 흐름을 먼저 완성한다.
- Desktop 각 Pane은 의미 있는 최소 너비를 가지며 Resize Handle은 Keyboard로도 조작한다.
- 2면 모드 기본 Pair는 `knowledge+conversation`이다. Studio를 활성화하면 `conversation+studio`, 자료를 활성화하면 `knowledge+conversation`으로 전환한다.
- 1면·Mobile 전환은 활성 면을 유지하고 숨은 면의 상태를 제거하지 않는다.

### 3.3 상태 보존 계약

단일 `WorkspaceViewState` 정본이 다음 필드를 소유한다.

`workspace_id | active_pane | secondary_pane | open_drawer | selected_source_id | conversation_id | run_id | run_status | artifact_id | artifact_cursor | evidence_id | evidence_position | pane_sizes | last_transition`

- 폭 변경은 표현만 바꾸며 위 업무 상태를 초기화하지 않는다.
- Pane·Drawer·Tab 전환도 다른 면의 상태를 초기화하지 않는다.
- 근거 Viewer를 닫고 다시 열면 동일 Evidence와 위치를 복원한다.
- Prototype Seed 상태는 Adapter 경계 안에 두고 화면에 `프로토타입 데이터`임을 표시한다.
- Runtime 오류·권한 차단·미연결 기능은 상태 모델에 명시하고 성공 상태로 자동 전환하지 않는다.

### 3.4 화면 구성

- 전역 Header: Workspace 이름, 현재 실행 상태, Layout Mode, Prototype 표시.
- 자료·지식 면: 선택 Source, 처리·권위·가중치 자리, 근거 열기 동작. 상세 업무 흐름은 M2-03 소유.
- 대화·실행 면: 대화 문맥, 실행 진행 상태, Citation 선택. 실제 모델 실행은 `unavailable`.
- 업무 Studio 면: 열린 산출물, 편집 위치, 생성 설정 진입 자리. 상세 수명주기는 M2-05 소유.
- 근거 Viewer: Source·Page/Time 위치, 닫기·복귀, 좁은 폭 전체 화면 동작.
- 필수 상태 `loading | empty | ready | warning | error | forbidden | unavailable`의 Layout 표현 경계를 제공하되, M2-02는 `ready`, `warning`, `unavailable` 대표 상태만 실제 클릭으로 검증한다.

### 3.5 접근성·설명 인터페이스

- M2-01 `accessibility-contract.json`과 Design Token을 직접 소비하고 값을 복제하지 않는다.
- Pane Switch, Drawer, Bottom Tab, Evidence Viewer, Resize Handle을 Keyboard로 열고 닫고 이동할 수 있어야 한다.
- Icon-only 동작은 Accessible Name과 Tooltip/Popover를 함께 가진다.
- 상시 설명 Box를 만들지 않는다. 오류·경고·진행은 Tooltip에만 숨기지 않는다.
- Focus는 가려지지 않고, `Escape`는 최상위 Drawer/Viewer를 닫은 뒤 원 Trigger로 Focus를 복원한다.
- Reduced Motion, OS 글꼴 확대, Touch 44px 우선 Target 계약을 유지한다.

## 4. 권장 산출물 경계

실제 구조와 충돌하면 근거를 제출하고 어울1 판단을 받는다. 승인 없이 새 Workspace Package나 외부 Runtime Dependency를 추가하지 않는다.

- `packages/ui/src/`: 적응형 Workspace Component·상태 모델·공용 Layout CSS
- `apps/web/app/`: 실제 Next 기반 Prototype Route와 실행 Harness
- `apps/web/`: `tsconfig`, Next 설정, Package Script의 최소 실행 경계
- `scripts/tests/` 또는 해당 Package Test: 경계값·상태 보존·접근성·금지 주소 계약 Test
- `docs/01_architecture/`: M3 승계 Layout·State·Adapter 계약
- `quality-gate-policy.json`: Source 등장으로 요구되는 Web/UI lint·type·unit·build Capability 명령만 최소 연결

기존 Lockfile의 승인된 Next·React·TypeScript만 사용한다. 새 Dependency 설치·버전 변경·Lockfile 변경은 금지한다.

## 5. 제외 범위

- M2-03 Source·지식·권위 세부 흐름
- M2-04 실제 Run·모델 Routing·Citation 조정 흐름
- M2-05 Studio 생성 설정·수명주기
- M2-06 계정·조직·정책·장치
- M2-07 운영·복구
- 실제 API/BFF/DB/Auth/Upload/LLM/Search/Export
- Windows·Android·iOS 실행 Shell과 M3 완료 선언
- Dark Theme, Brand·Marketing 화면
- 성공으로 보이는 임시 Backend Mock

## 6. 기존 기능과 불변조건

- M2-01의 Route ID, Screen ID, Token 값, 접근성 계약과 Action Major를 바꾸지 않는다.
- 독립 저장소·7범주 품질 Gate·기존 25개 Gate Test를 유지한다.
- Browser 코드의 API 절대주소, `localhost`, `127.0.0.1`, Docker 내부 주소·Port, `NEXT_PUBLIC_API_BASE_URL`을 금지한다.
- Daon 승인 지식·RuleSet 우선, Vision/LLM-first, Parser/OCR 보조 원칙을 화면 문구에서 뒤집지 않는다.
- 기존 `shared-db`, `common`, `netdata`, `proxy`를 사용하거나 변경하지 않는다.
- 요구되지 않은 Refactor, 전체 재작성, 설정·의존성 임의 변경을 금지한다.

## 7. 실행 단계

| 단계 | 작업 | 완료조건 |
| --- | --- | --- |
| S0 | 정본·Hash·기준 Commit·현재 Diff·M2-01 계약 확인, 진행 기록 착수 | 범위·선행 변경·보호 파일 확인 |
| S1 | 실패하는 Layout 경계·상태 보존·접근성·금지 주소 Test 작성 | 승인 계약 누락이 Red로 재현됨 |
| S2 | UI 상태 모델과 Layout Mode 결정 구현 | 6개 경계값과 업무 상태 불변 PASS |
| S3 | React 3면 Workspace·근거 Viewer 구현 | 1920×1080 세 면·Resize·Viewer 동작 |
| S4 | 2면·1면·Mobile Bottom Tab 구현 | 네 구간 전환과 활성 면·숨은 상태 보존 |
| S5 | Keyboard·Focus·Tooltip/Popover·대표 오류 상태 구현 | M2-01 접근성 계약 PASS |
| S6 | Next Prototype Harness와 품질 Gate 연결 | 새 외부 Dependency 없이 Build·Gate PASS |
| S7 | 실제 Browser 네 폭 클릭 검증 | `1440+`, `1024~1439`, `600~1023`, `599-` 전환·상태 보존·Console/Network 확인 |
| S8 | 로컬 전체 회귀·Diff 검증 후 Hand-off | `HANDOFF_READY`, 어울1 Commit·Push 대기 |
| S9 | 불변 SHA GitHub CI·ysna-server ARM64 검증 | Build·Gate·Migration N/A·자원 불변·Artifact PASS |
| S10 | Evidence·결과보고 | 완료조건 전수 대조와 정식 상태 제출 |

S8 이후 구현 코드를 수정하지 않는다. 어울1이 Diff를 검토해 Commit·Push한 불변 SHA를 전달한 뒤 같은 어울2가 S9부터 재개한다.

## 8. 테스트와 증거

### 필수 자동 검증

- Layout 경계값 6개와 네 `layout_mode` 정확성
- 세 Pane ID·Drawer·Bottom Tab·Evidence Viewer 누락 0
- 폭·Pane 전환 전후 `WorkspaceViewState` 업무 상태 전부 동일
- 근거 Viewer 위치·Studio 편집 Cursor 복원
- Keyboard, Focus 복원, Accessible Name, Tooltip/Popover 계약
- M2-01 Token·Route·Screen·접근성 정본 직접 소비
- `unavailable`·Prototype Adapter 표시와 성공 위장 0
- Browser Source 금지 URL·환경변수 0, 실제 Network API 요청 0
- Next Production Build·Typecheck·신규 Test
- 기존 품질 Gate Test 25건, Product Foundation 8건, Toolchain, 독립성, 7범주 Gate PASS
- `git diff --check`, 추적 삭제 0, 허용 범위 밖 변경 0

### 실제 Browser 증거

- 기준 1920×1080과 각 반응형 구간 대표 폭 Screenshot
- 각 폭에서 Pane/Drawer/Tab/Evidence Viewer 실제 클릭
- 폭 변경 전후 선택 Source·대화·Run·산출물 Cursor·근거 위치 대조
- Keyboard만으로 Drawer/Viewer 열기·닫기·Focus 복원
- 계산된 기본 Font `12px`, 제목 `16px`, Touch Target과 Layout Mode 확인
- Console Error 0, Network에서 API 호출 0 및 내부 주소 노출 0

### 서버·GitHub 증거

- 정확 Push SHA, Clean Checkout, ARM64 Build·Test
- Schema/Migration 경로가 없으면 `NOT_APPLICABLE_NO_SCHEMA`, DB 명령 0
- ysna-server 기존 Container·Network·Volume 사전·사후 Hash 불변과 임시 자원 0
- PR Required Check, Branch Protection, Artifact PASS·Exit 0·7범주·Failures 0

## 9. 진행 복구 기록

진행 파일은 `docs/04_test_reports/release_1/R1-M2-02_progress.md`다.

어울2는 착수, 각 세부 단계 완료, 오류 발생·원인·복구, Test 완료, Browser 검증, Hand-off와 종료 직전에 다음을 즉시 기록한다.

`시각 | 단계 | 상태 | 변경 파일 | 명령·Exit | 검사 결과 | 오류·원인 | 복구·대안 | 증거 경로 | 남은 위험 | next_action`

장시간 설치·Build·서버·Browser 명령은 충분히 기다리고, 동일 명령을 근거 없이 중복 실행하지 않는다. 예기치 않은 중단은 실패보고로 집계하지 않고 마지막 성공 단계부터 이어간다.

## 10. 결과보고 계약

결과보고 경로는 `docs/02_work_orders/reports/R1-M2-02_attempt-1.md`다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | Browser 증거 | 미해결 사항 | 다음으로 필요한 판단`

검토 출력은 `판정 → 판단 이유 → 조치` 순서로 작성한다. 중대 미진은 별도 수정 작업지시서로 회부하고, 합격 가능한 경미 보완은 다음 작업지시서에 흡수한다. 사소한 이유로 합격 작업 전체를 다시 열지 않는다.

## 11. 승인 경계

- 위 Layout·State·Prototype·품질 Gate 계약 안의 구현 방법은 어울1 판단 범위다.
- 기능 범위·요구사항·공개 API·데이터 계약·보안 경계·중요 위험을 바꾸려면 쓰기를 중지하고 신산님 승인을 요청한다.
- Commit·Push·PR·Branch Protection 변경은 어울1이 수행한다.
- 외부 운영 배포·파괴적 작업은 이번 범위가 아니다.

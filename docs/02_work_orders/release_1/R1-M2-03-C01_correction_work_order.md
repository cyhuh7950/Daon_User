# 수정 작업지시서 R1-M2-03-C01 · Source Prototype 계약 정합

## 0. 판정

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M2-03-C01` |
| 원 issue_id | `R1-M2-03-I001` |
| Attempt 판정 | `REWORK_REQUIRED` · 동일 작업 `INCOMPLETE 1/3` |
| 유효 FAILURE_REPORT | `0회` |
| 실행 | 동일 어울2 · 단독 코드 작성자 |
| 실행 기준 | `codex/r1-m2-03` · 미커밋 Attempt 1 상태 보존 |
| 작성일 | `2026-07-21` |

R1-M2-03 원 작업지시서와 승인 정본은 계속 유효하다. 아래 결함만 TDD로 최소 수정하며 정상 구현을 전체 재작성하지 않는다.

## 1. 중대 미진과 수정 계약

### C1. 가중치 실제 적용 계층

- 현재 UI는 그룹·유형·기본값을 초기값으로 읽은 뒤 무조건 `source` 값을 주입해 모든 Source의 적용 계층을 `source`로 오표시한다.
- 최초 화면은 원 `weightProfile` 그대로 `resolveWeight()`하여 `source | group | type | default`의 실제 최근접 계층을 표시한다.
- 사용자가 개별 Source Override를 명시적으로 추가·변경·해제할 수 있게 한다. Override 추가 후만 `source`, 해제 후에는 다시 group/type/default 최근접 계층으로 돌아간다.
- `0.5~2.0`, `0.1`, 조직 Clamp, 비곱셈 계약은 유지한다.
- group·type·default 각각의 실제 UI Snapshot과 Override 추가·해제 상태 전이를 React 렌더 기반 Test로 검증한다.

### C2. 선택 Source–Version–Evidence Viewer 계보

- Evidence 열기 시 선택 Source의 `source_id`, 현재 `source_version_id`, Evidence ID·표시 이름·위치(Page/Cell/Region/시간 구간)를 단일 Workspace 상태에 기록한 뒤 Viewer를 연다.
- Viewer는 고정 PDF·고정 Page·고정 인용문을 표시하지 않고 현재 선택 Source의 Evidence Snapshot을 렌더링한다.
- 문서, 오디오 직접 이해, ASR+LLM 각각에서 서로 다른 Source·Version·위치가 정확히 열리는지 검증한다.
- 닫기·재열기와 Pane/폭 전환 뒤에도 동일 Evidence와 위치·Trigger Focus가 보존돼야 한다.

### C3. Domain 상태 보존

- `activeTab`, 등록 Panel, 선택 Version, RuleSet 토글, 충돌 검토 결과, 가중치 Override를 자료·지식 Pane의 로컬 수명에 두지 않는다.
- M2-02 단일 상태 Adapter 원칙을 유지하도록 `AdaptiveWorkspace`가 소유하는 `SourceKnowledgeViewState` 또는 동등한 상위 상태 정본을 만들고 Pane에는 값·전이 Callback을 전달한다.
- Bottom Tab, single/two-pane, Drawer 전환으로 Pane이 언마운트·재마운트되어도 위 Domain 상태가 유지돼야 한다.
- 기존 `WorkspaceViewState` 공개 필드와 M2-02 Layout·Modal·Focus 계약을 깨지 않는다.

### C4. 활성 무동작 Control 금지

- `검토 요청`, `사용 중지`가 이번 Prototype에서 상태 전이를 수행하면 Audit Preview와 결과 상태를 실제로 갱신한다. 실제 Run이 필요한 `재처리 요청`은 disabled+`unavailable`을 유지한다.
- 승인·외부 전달·생산 지식 등록은 M2-05 이후 실행 범위이므로 충돌 해결 후에도 명시적 `Prototype · unavailable` 또는 disabled 상태로 남긴다. 실행 가능한 Control처럼 보이는 무동작 버튼을 금지한다.
- 모든 enabled Button은 관찰 가능한 상태 전이·Dialog/Popover·명시 Navigation 중 하나를 가져야 한다는 회귀 Test를 추가한다.

### C5. 과거 Version 실제 열람

- 최소 두 Source에 현재 Version과 하나 이상의 과거 불변 Version을 제공한다. 문서 1건과 오디오 1건을 포함한다.
- Version 선택 시 Digest·capturedAt·previousVersionId·Evidence 위치가 선택 Version 기준으로 바뀐다.
- 과거 Version 객체는 변경되지 않고 새 Version 생성만 허용하는 기존 계약을 유지한다.

### C6. 중요 충돌 자동 판정

- 불변 `ConflictPolicyVersion`과 입력 사실로 `informational | material | critical`을 결정론적으로 반환하는 순수 판정 함수를 구현한다.
- 최소 규칙은 상세 설계 §7.4를 그대로 반영한다: 최종 결과 영향 없음→informational, 동일 Tier 중요 주장 미해결→material, 활성 강제 RuleSet/Daon 승인 지식과 실제 결과에 영향 주는 미해결 충돌→critical.
- Seed는 이 판정 함수의 결과로 생성하며 severity를 독립 상수로 미리 적지 않는다.
- 세 심각도, 검토자 상향만 허용, 정책 잠금 하향 금지, 미해결 중요 충돌 3종 최종화 차단을 Test한다.

### C7. Tooltip 접근성

- Help Trigger가 열린 Tooltip의 ID를 `aria-describedby`로 연결한다. 닫힌 상태에서는 잘못된 참조를 남기지 않는다.
- Focus/Hover/Click 열기와 Escape/Blur 닫기, accessible description 연결을 React 렌더 또는 실제 DOM Test로 고정한다.

## 2. 보호·금지 범위

- 새 외부 Dependency·Lockfile·Package·API·DB·실제 Upload/LLM/Index를 추가하지 않는다.
- M2-01·M2-02 Route·Token·Layout·Resize·Modal·Focus 계약과 기존 정상 기능을 유지한다.
- 기존 R1-M1-04 Dirty 두 파일을 수정·복원·Stage하지 않는다.
- 현재 정상 구현을 전체 재작성하거나 요구되지 않은 Refactor를 하지 않는다.
- 첫 Attempt의 Browser JSON·Manifest·PNG는 교정 전 증거이므로 정본으로 재사용하지 않는다.

## 3. 재작업 단계

| 단계 | 작업 | 완료조건 |
| --- | --- | --- |
| C0 | 본 문서·원 지시서 EOF, 현재 Diff·보호 파일 확인 | `REWORK_ATTEMPT_1`, 쓰기 범위 고정 |
| C1 | 위 7건을 재현하는 실패 Test 추가 | UI 오배선·상태 초기화·Evidence 불일치·무동작·Version·판정·ARIA가 Red |
| C2 | 가중치 계층·Override와 과거 Version 최소 수정 | 실제 계층·선택 Version Green |
| C3 | 상위 Domain 상태 정본과 Source Evidence Snapshot 연결 | 재마운트·Viewer 계보 Green |
| C4 | Control 상태 전이·unavailable와 충돌 판정·Tooltip 수정 | enabled 무동작 0, 세 severity·ARIA Green |
| C5 | 신규·M2-02 전체 회귀, Lint·Build·7범주 Gate | 전부 PASS, 새 의존성 0 |
| C6 | Production Browser 1920/1200/800/500 재검증 | 가중치 4계층, Version, Source Evidence, 재마운트 보존, Control, 충돌, Tooltip PASS |
| C7 | Browser JSON·PNG 4건·Manifest 전부 재생성 | 실제 화면·DOM·Hash 정합, Console/금지 Network 0 |
| C8 | Diff·보호 범위·Attempt 2 보고 | `HANDOFF_READY`, 구현 쓰기 중지 |

## 4. 진행·결과보고

- 기존 `docs/04_test_reports/release_1/R1-M2-03_progress.md`에 C0부터 각 단계·오류·원인·복구·Test·증거를 즉시 이어 기록한다.
- 결과보고는 `docs/02_work_orders/reports/R1-M2-03_attempt-2.md`다.
- Commit·Push·PR은 금지하고 C8 이후 어울1 판단을 기다린다.
- 기능 범위·요구사항·공개 API·데이터·보안 계약 변경이 필요하면 쓰기를 중지하고 어울1에게 증거와 함께 회부한다.

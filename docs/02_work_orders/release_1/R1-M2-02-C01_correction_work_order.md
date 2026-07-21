# 수정 작업지시서 R1-M2-02-C01 · Layout·접근성·증거 정합성 보완

## 0. 판정과 문서 정보

| 항목 | 값 |
| --- | --- |
| 원 Work Order | `R1-M2-02` |
| 수정 Work Order | `R1-M2-02-C01` |
| issue_id | `R1-M2-02-I001` |
| 판정 | `REWORK_REQUIRED` · 중대 미진 |
| 집계 | `INCOMPLETE 1/3`, 유효 `FAILURE_REPORT 0회` |
| 작성·기술 판단 | 어울1 |
| 실행 | 동일 어울2 · `daon-developer` |
| 기준 Branch | `codex/r1-m2-02` |
| 기준 Commit | `aa071226f1793a089b2488e3a57bfc1db4496b05` + 현재 미커밋 Attempt 1 상태 |

원 작업의 범위·요구사항을 변경하지 않는다. `R1-M2-02_work_order.md`와 승인 정본은 계속 유효하며, 이 문서는 Attempt 1 검토에서 발견된 중대 미진의 수정 계약만 추가한다.

## 1. 검토 판정 근거

자동 Test·Build·Browser 대표 흐름은 통과했으나 아래 계약 결함과 테스트 사각지대 때문에 `HANDOFF_READY`를 수락할 수 없다.

1. Pane Resize가 이웃 면의 최소 폭을 보장하지 않아 `13%`, `-3%`까지 재현된다.
2. `aria-modal` Drawer·Viewer가 열릴 때 내부 Focus 이동, Tab/Shift+Tab 순환, 배경 비활성화가 없다. Drawer 안에서 Viewer를 열면 Trigger가 unmount되어 복귀 Focus도 유실된다.
3. `i` 도움말이 `title`만 가지며 Hover·Focus·Touch·Dismissible Tooltip/Popover로 동작하지 않는다.
4. Resize Handle Pointer Target이 `10px`로 M2-01 최소 `24px`보다 작다.
5. Browser Evidence의 Run·Artifact·Evidence 위치가 실제 Seed·Screenshot과 다르다.
6. `/`가 M2-01 `home` Route가 아니라 `workspace_detail`을 렌더링한다.
7. 품질 Gate의 `lint`가 실제 Lint가 아니라 Unit Test Alias다.
8. Breakpoint·Target 값 복제와 실제 동적 Route 누락 등 자동 Test 사각지대가 있다.

## 2. 필수 수정 계약

### C1. Resize 불변조건

- Resize 대상과 이웃 Pane을 모두 `20~55` 범위로 제한한다.
- 한 번의 Resize 전후 세 Pane 합계는 동일해야 한다.
- 큰 양수·음수 Delta와 반복 Keyboard·Pointer 입력에서도 음수·`20` 미만·`55` 초과가 없어야 한다.
- 회귀 Test는 최소 `{30,38,32}`에서 `+100`, `-100`, 반복 조작과 실제 `{55,13,32}`, `{55,48,-3}` 재현 입력을 포함한다.

### C2. Modal Focus·Keyboard

- Drawer·Evidence Viewer가 열리면 첫 의미 있는 Control 또는 닫기 Button으로 Focus를 이동한다.
- `Tab`과 `Shift+Tab`은 최상위 Modal 내부에서 순환한다.
- Modal 외 배경은 실제 `inert` 또는 동등한 Keyboard/Pointer 차단으로 비활성화한다. Modal 자체를 함께 비활성화하면 안 된다.
- `Escape`는 최상위 Modal만 닫고 원 Trigger로 Focus를 복원한다.
- Drawer 내부에서 Evidence Viewer를 열면 닫을 때 Drawer 문맥을 복원하고 Drawer 안 Evidence Trigger 또는 명시된 안전한 복귀 Control에 Focus를 돌린다.
- 자동 Test와 실제 Chrome Keyboard Test 모두 제출한다.

### C3. 도움말 Interface

- `InfoButton`은 Accessible Name 외에 실제 Tooltip 또는 Popover를 가진다.
- Hover·Focus·Touch/Click으로 열 수 있고 `Escape`, Blur 또는 명시 닫기로 해제할 수 있어야 한다.
- `aria-describedby`, `aria-expanded`, `role=tooltip`/Popover 등 선택한 Pattern의 접근성 관계를 명시한다.
- `title` 속성만으로 완료 처리하지 않는다.

### C4. Target·Token 정본

- Resize Handle의 실제 Pointer Hit Area를 `var(--daon-target-minimum)` 이상으로 만든다. 시각선은 좁게 유지해도 된다.
- Desktop·Touch Control은 기존 Token 변수를 직접 사용한다.
- CSS Media Query의 숫자 복제가 불가피하면 `tokens.json` 정본과 6개 경계값 일치를 자동 Test로 강제하고 생성·승계 이유를 문서화한다.
- `32px`, `24px`, `44px`를 새 UI 계약 값으로 직접 복제하지 않는다.

### C5. Route 정합

- `/workspaces/[workspace_id]`만 `workspace_detail` Prototype을 렌더링한다.
- `/`는 M2-01 `home` Route·Screen 계약을 보존하는 최소 Home Shell을 렌더링하고 Workspace Prototype 진입 링크를 제공한다.
- Home의 미구현 기능은 `unavailable`/Prototype으로 명시하며 M2-03 이후 기능을 구현하지 않는다.
- Route·Screen 정본 존재와 실제 Page 매핑을 자동 Test로 검증한다.

### C6. 실제 Lint와 Test 사각지대

- `lint` Capability는 `verify:workspace` Unit Alias를 사용하지 않는다.
- 새 Dependency·Lockfile 변경 없이 승인된 TypeScript Compiler API 또는 동등한 기존 도구로 JS/JSX Parse·정적 규칙을 수행하는 별도 `lint:workspace` 명령을 구현한다.
- 최소 규칙은 Parse Diagnostic, `debugger`, `eval`, 금지 Browser URL/환경변수, 이번 범위의 직접 `fetch`를 검출하고 비정상 Fixture가 Exit 1이 되는 Test를 가진다.
- Next Build는 Build Capability, Workspace 계약 Test는 Unit/Contract, 새 Lint는 Lint Capability로 서로 다른 의미를 유지한다.
- same-origin·M2-01 소비 Test 대상에 실제 동적 Route `apps/web/app/workspaces/[workspace_id]/page.jsx`를 포함한다.
- 문자열 존재만으로 Focus·Tooltip·Target Size를 PASS시키지 말고 상태 함수/DOM 상호작용/산출 CSS 계약을 검증한다.

### C7. Browser Evidence 정정

- 수정 후 1920×1080, 1200×900, 800×900, 500×900 실제 Chrome 검증을 다시 수행한다.
- Resize 최소폭·합계, Modal 최초 Focus·Tab/Shift+Tab 순환·배경 비활성·Escape 복귀, Drawer→Viewer→Drawer 복귀, Tooltip/Popover Hover·Focus·Touch/Click·Dismiss를 포함한다.
- `browser-validation.json`의 `run_id`, `artifact_id`, `evidence_position`은 실제 DOM `data-*`와 Screenshot/상호작용 결과의 정확한 전체 문자열을 기록한다.
- Evidence Manifest Hash를 새 파일 기준으로 다시 계산한다.
- Console Error 0, API 요청 0, non-same-origin 0, 금지 주소 0을 재확인한다.

## 3. 수정 금지·보호 범위

- 기능 범위, 공개 API, 데이터·보안 계약과 M2-03 이후 기능을 추가하지 않는다.
- 새 외부 Dependency·Lockfile 변경을 금지한다.
- M2-01 Route·Screen·Token·접근성 정본의 값을 임의로 바꾸지 않는다.
- 기존 R1-M1-04 Dirty 2건과 R1-M1-05 Evidence를 수정·Stage하지 않는다.
- Attempt 1의 정상 구현을 전체 재작성하지 않고 결함 부위와 Test만 최소 수정한다.

## 4. 재작업 단계

| 단계 | 작업 | 완료조건 |
| --- | --- | --- |
| C0 | 본 수정지시·검토 근거 확인, progress에 `REWORK_ATTEMPT_1` 기록 | 8개 결함과 보호 범위 확인 |
| C1 | 결함별 실패 회귀 Test 작성 | Resize·Modal·Help·Route·Lint·Evidence 결함이 Red로 재현됨 |
| C2 | Resize·Target·Token 정합 수정 | 양쪽 최소폭·합계·Hit Area·Token Test PASS |
| C3 | Modal Focus·Tooltip/Popover 수정 | 자동 Test와 Keyboard 동작 PASS |
| C4 | Home Route·실제 Lint·Test 범위 수정 | Route mapping·Lint negative fixture·동적 Route 검사 PASS |
| C5 | 실제 Browser 네 폭 재검증·Evidence 재생성 | C7 전 항목과 정확한 DOM 값·Hash PASS |
| C6 | 전체 회귀 | Workspace, Foundation, Gate 25, Lint, Toolchain, Independence, Next Build, 7범주 Gate PASS |
| C7 | Diff·보호 파일·보고서 최종화 | 추적 삭제 0, 범위 밖 Diff 0, `HANDOFF_READY` 재제출 |

## 5. 진행·결과보고

- 기존 `docs/04_test_reports/release_1/R1-M2-02_progress.md`에 재작업 단계와 각 오류·복구를 이어 기록한다.
- 기존 `docs/02_work_orders/reports/R1-M2-02_attempt-1.md`는 Attempt 1 사실을 보존한다.
- 재작업 결과는 `docs/02_work_orders/reports/R1-M2-02_attempt-2.md`에 작성한다.
- 결과 형식은 `판정 → 판단 이유 → 조치`와 `status | issue_id | 수행한 수정 | 테스트 결과 | Browser 증거 | 미해결 사항 | 다음 판단`을 따른다.
- C7 `HANDOFF_READY` 뒤 구현 쓰기를 중지하고 어울1 검토·Commit·Push를 기다린다.

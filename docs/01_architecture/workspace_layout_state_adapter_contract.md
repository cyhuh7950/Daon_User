# 적응형 Workspace Layout·State·Adapter 승계 계약

## 판정

`R1-M2-02`는 M3 Web·Windows Shell이 재사용하는 Production-bound Workspace 기준선이다. Domain 실행이나 API를 구현하지 않으며, 아직 연결되지 않은 실행은 `unavailable`로 유지한다.

## Layout 계약

| 폭 | mode | 표시 |
| --- | --- | --- |
| `1440px+` | `three-pane` | 자료·지식, 대화·실행, Studio 동시 표시와 Keyboard·Pointer Resize |
| `1024~1439px` | `two-pane` | 기본 자료+대화, Studio 활성 시 대화+Studio, 숨은 면 Drawer |
| `600~1023px` | `single-pane` | 활성 면 하나와 보조 Drawer |
| `599px-` | `bottom-tabs` | 활성 면 하나와 하단 3 Tab, 근거 Viewer 전체 화면 |

경계값 판정 정본은 `packages/ui/src/workspace-model.js`의 `getLayoutMode`다. React 표현은 `packages/ui/src/adaptive-workspace.jsx`, 공용 Layout CSS는 `packages/ui/src/workspace.css`가 소유한다.

## 상태 정본

`WorkspaceViewState`는 다음 필드를 한 객체에서 보존한다.

`workspace_id | active_pane | secondary_pane | open_drawer | selected_source_id | conversation_id | run_id | run_status | artifact_id | artifact_cursor | evidence_id | evidence_position | pane_sizes | last_transition`

폭 변경은 `projectWorkspace`의 표현 Projection일 뿐 상태를 수정하지 않는다. Pane·Drawer·Tab 전환은 `transitionWorkspace`를 사용하며 선택 Source, 대화, Run, 산출물 Cursor와 Evidence 위치를 초기화하지 않는다.

## Adapter 경계

- 현재 Seed는 UI 상태 모델 내부의 Prototype Adapter 데이터이며 Header에 `프로토타입 데이터`를 표시한다.
- 실제 Run은 `unavailable`이고 Network 요청을 만들지 않는다.
- M3는 `AdaptiveWorkspace`와 상태 모델을 그대로 소비한다.
- M4 이후 BFF Adapter는 Browser same-origin 상대 경로에서만 연결한다. 내부 주소·Provider URL·Secret은 Component나 상태에 넣지 않는다.
- M2-03~05는 각 Pane의 상세 Domain 흐름을 확장하되 Layout·상태 보존·접근성 공개 계약을 바꾸지 않는다.

## 접근성 계약

M2-01 `packages/ui/accessibility-contract.json`과 `packages/design-tokens/tokens.css`를 직접 소비한다. Pane Switch·Drawer·Bottom Tab·Evidence Viewer·Resize Handle은 Keyboard 접근이 가능하고, `Escape`는 최상위 Overlay를 닫은 뒤 Trigger에 Focus를 복원한다. Icon-only Control은 Accessible Name과 Tooltip을 함께 가진다.

## 회귀 경계

- M2-01 Route ID·Screen ID·Token·접근성 계약은 수정하지 않는다.
- 새 외부 Dependency·Lockfile·Backend/API/DB/Auth는 추가하지 않는다.
- Browser Source의 API 요청, 절대주소, `localhost`, Docker 내부 주소와 `NEXT_PUBLIC_API_BASE_URL`은 0건이어야 한다.

## Breakpoint·Target 정본 유지

- CSS Media Query는 현재 브라우저가 Custom Property를 조건식 값으로 사용할 수 없어 M2-01 `tokens.json`의 `1439`, `1023`, `599` 경계를 복제한다. 자동 Test가 JSON의 6개 경계값과 CSS Query를 함께 대조해 Drift를 차단한다.
- Resize Pointer Hit Area, Desktop Control, Touch Control은 각각 `--daon-target-minimum`, `--daon-target-desktop-control`, `--daon-target-touch-control`을 직접 사용한다. 시각 Resize 선만 Hit Area 중앙에 좁게 표현한다.
- `/`는 M2-01 `home` Route·Screen을 보존하는 최소 Shell이고, Workspace Prototype은 `/workspaces/[workspace_id]`에서만 렌더링한다.

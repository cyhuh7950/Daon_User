# Foundation B10 Operations Gate

- 시각: 2026-08-15 06:36 KST
- 범위: Phase B 메뉴 10 `운영상태`
- Runtime: Workspace `Action.VIEW` 권한 뒤 Provider/API/Storage/Sync/Queue 5개 안전 Projection 반환
- BFF: `GET /bff/api/workspaces/workspace-b10/operations/status`만 same-origin 전달, query 제거, POST 거부
- Web Adapter: exact data/meta, 5개 component 순서·상태·safe code·pending count·recovery action 외 응답 거부
- React: Provider, API, Storage, Sync, Queue와 `동기화 설정 열기`, `상태 새로고침` 표시

## TDD와 자동 검증

- API Domain/Runtime focused: `5 passed`
- BFF/Adapter/Web actual React: `37 passed`
- OpenAPI: `23 passed`; R1-M8 profile `80 paths / 100 operations / 133 schemas / 31 errors`
- OpenAPI canonical SHA-256: `087A0A653766F294D4335A6943DFD87E3242FADD74D9F4E36879E88F606C844D`
- Web production build/TypeScript/8 pages: PASS
- Web boundary: `269 files / violations 0`

## Actual PostgreSQL 15

- disposable DB: `daon_b10_operations_it_20260815`
- migration: `0001 -> 0016` PASS
- 제품 `PostgresOperationsStatusRepository`: 현재 Workspace sync pending `1`, queue pending `0`, queue failed `0`
- 다른 Tenant/Workspace의 pending operation은 현재 집계에 포함되지 않음
- focused: `3 passed`
- `B10_CURRENT_0016_PASS`
- `B10_OPERATIONS_RLS_COUNTS_PASS`
- `B10_CLEANUP_REMAINING_0`

## Actual Browser 1920x1080

- local production build Web `14190`, API fixture `18490`
- Source/Knowledge/Studio 초기 GET: 모두 HTTP 200
- 운영상태 same-origin GET: HTTP 200
- 실제 클릭 후 5개 component와 warning pending `Sync 2건`, `Queue 3건` 표시
- `상태 새로고침` 실제 클릭 후 component 5개 유지
- `동기화 설정 열기` 실제 클릭 후 운영상태 dialog 닫힘, Workspace 설정 menu 표시
- console warning/error: 0
- 화면 내부 URL, SQLSTATE, Traceback, secret 노출: 0
- screenshot: `foundation-b10-operations-1920x1080.png`
- screenshot SHA-256: `8750BBC598164BDCB47F68E8F472C7F419271B7F6CC10531BAB266B1BC8DB0AB`
- Browser tab finalized, viewport reset, ports `14190/18490` listener 0, temp log/pyc 0

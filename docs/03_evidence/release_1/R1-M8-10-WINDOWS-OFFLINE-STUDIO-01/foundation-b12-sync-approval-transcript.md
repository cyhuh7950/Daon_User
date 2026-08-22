# Foundation B12 — 동기화·승인 실제 검증

- 실행 시각: 2026-08-15T07:33:28+09:00
- 범위: Workspace 설정의 동기화 Preview 조회, 항목별 명시 선택, Step-up 승인, 상태 재조회
- 자동 전송: 없음

## RED → GREEN

- RED: `SyncOperationView.item_ids`, Workspace 목록 API, Web same-origin Adapter/BFF, 설정 Popup 동작 부재를 각각 계약 테스트로 확인했다.
- GREEN: Workspace 범위 목록과 deterministic ETag, 선택 항목만 승인하는 Step-up 흐름, exact replay와 scope-expansion 거부를 구현했다.
- OpenAPI R1-M8 evidence: paths 81, operations 103, schemas 138, errors 31, SHA-256 `754A6DDC859E006A02327A380FD53086532E98B0EF2A14936E843658173CE5E8`.

## 실제 PostgreSQL 15 Gate

- Alembic `0001 → 0017` 적용
- Workspace 소유 목록 1건, Preview item IDs 1세트 확인
- 선택 항목 승인 및 동일 요청 exact replay 확인
- 다른 Workspace 목록 0건과 RLS 격리 확인
- 결과: `SYNC_APPROVAL_SETTINGS_PG_GATE PASS list=1 item_ids=1 approval=exact replay=exact rls_cross=0`
- 종료: disposable DB cleanup 수행

## 실제 Browser 1920×1080 Gate

- 격리 Web `127.0.0.1:14192`, 내부 API `127.0.0.1:18492`; 브라우저는 same-origin `/bff/api/*`만 사용했다.
- 설정 → 동기화·승인에서 `item-source-b12`, `item-output-b12` 두 항목이 기본 선택된 Preview를 확인했다.
- 현재 비밀번호는 Step-up 요청에만 사용했으며 증거에 원문을 기록하지 않았다.
- 실제 Network 흐름:
  - `GET /api/v1/workspaces/workspace-b12/sync-operations` → 200
  - `POST /api/v1/session/step-up` → 201
  - `POST /api/v1/sync-operations/sync-operation-b12/approve` → 200
- 최종 UI: `승인됨`, 항목 2개, Version 2
- 서버 확인: `SYNC_APPROVED=["item-source-b12", "item-output-b12"]`
- 브라우저 console warning/error 0, 내부 API 주소·SQLSTATE·Traceback 노출 0
- 화면 증거: `scripts/tests/web-final-ui-evidence/foundation-b12-sync-approval-1920x1080.png`

## Fresh 회귀와 정리

- Node OpenAPI/BFF/Adapter/actual React: 65/65 PASS
- Python Sync Domain/Runtime/PostgreSQL/Contract: 11 PASS, 1 SKIP, 3 subtests PASS
- Web production build·TypeScript·8 pages, boundary 270/0 PASS
- Browser viewport reset/finalize, 포트 14192/18492 listener 0, 격리 temp 0
- `git diff --check` PASS, staged 0

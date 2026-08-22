# Foundation B13 — 조직 정책 읽기 전용 실제 검증

- 실행 시각: 2026-08-15T07:50:00+09:00
- 범위: Workspace 설정에서 조직 Egress 강제 정책과 Workspace 적용 결과 조회
- 변경 권한: 없음. 이 Popup은 읽기 전용이다.

## RED → GREEN

- RED: 설정 메뉴의 조직 정책 항목이 disabled였고, effective Egress 응답의 exact safe projection 검증과 Popup이 없었다.
- GREEN: 기존 Workspace Egress GET을 strict same-origin Adapter에 연결하고, 조직 정책 8필드와 Workspace 적용 결과를 읽기 전용으로 표시했다.
- 기존 조직 관리자 전용 편집 화면·Step-up·ETag·권한 계약은 변경하지 않았다.
- 응답의 unknown field, 내부 URL, invalid policy payload는 `EGRESS_POLICY_RESPONSE_INVALID`로 fail-close한다.

## 실제 PostgreSQL 15 Gate

- disposable DB에서 Alembic `0001 → 0011`, Tenant/Workspace 2개 seed, `0011 → 0017` 적용
- 두 Scope에 deterministic 조직·Workspace 기본 Egress Policy/Binding 생성 확인
- 현재 Workspace projection: `deny_external`, `parent_locked=true`
- 조직 정책 8필드와 organization/workspace ETag 확인
- 두 Tenant의 policy/binding ID가 서로 다름을 확인
- 결과: `ORGANIZATION_POLICY_PG_GATE PASS scopes=2 effective=deny_external parent_locked=true cross_scope=distinct`
- 종료: `daon_foundation_b13_20260815` remaining 0

## 실제 Browser 1920×1080 Gate

- 격리 Web `127.0.0.1:14193`; 브라우저는 same-origin `/bff/api/workspaces/workspace-b13/egress-policy`만 호출했다.
- 설정 → 조직 정책 실제 클릭, downstream Egress GET 200
- 조직 강제 정책 8필드: 승인된 외부 전송, external_api, api.example.com, internal, 4,096 bytes, 마스킹 필수, 삭제 처리 선택, 조직 관리자
- `Workspace 적용 결과`와 정책 교집합 안내 확인
- input/select/textarea 0으로 읽기 전용 보장
- opaque policy/binding ID, fingerprint, ETag, 내부 API 주소, SQLSTATE, Traceback 노출 0
- Browser console warning/error 0
- 화면 증거: `scripts/tests/web-final-ui-evidence/foundation-b13-organization-policy-1920x1080.png`, SHA-256 `FBB545DB7D54D37777D99AA253FD3ADFCE6EE971C8A1750914CD07617118A9AE`
- 정리: ports 14193/18493 listener 0, temp 0, viewport reset/finalize

## Fresh 회귀

- Egress API/Runtime/PostgreSQL contract: 7 PASS
- BFF/Egress/actual React: 45/45 PASS
- Phase A·B 주요 Node: 124/124 PASS
- API 전체: 405 PASS, 29 SKIP, 137 subtests PASS
- Web production build·TypeScript·8 pages PASS, boundary 273/0
- OpenAPI R1-M8: paths 81, operations 103, schemas 138, errors 31, SHA-256 `754A6DDC859E006A02327A380FD53086532E98B0EF2A14936E843658173CE5E8`

# Foundation B11 출력·Version 실제 Gate

- 실행일: 2026-08-15 (Asia/Seoul)
- 범위: `설정 → 출력·버전`
- 공개 경계: `GET|PATCH /api/v1/workspaces/{id}/output-version-settings`
- Browser 경계: same-origin `/bff/api/workspaces/{id}/output-version-settings`

## PostgreSQL

- WSL `local-postgres`의 고유 disposable DB에서 `0001→0017` PASS.
- Workspace 기본값은 Version 0, 최초 저장은 Version 1.
- 동일 actor·Idempotency-Key·요청은 Version 1 exact replay.
- 동일 Key에서 `expected_version` 변경은 `IDEMPOTENCY_KEY_REUSED`로 거부.
- `daon_app` 강제 RLS: own row 1, cross-tenant row 0.
- `0017→0016→0017` rollback/reapply PASS.
- `daon_foundation_b11_*` DB remaining 0, `local-postgres` 유지.

## Browser 1920×1080

- current-source production Web build를 14191, fixture API를 18491에 격리 기동.
- Browser 요청은 `/bff/api/workspaces/workspace-b11/output-version-settings` same-origin만 사용.
- 5개 출력 유형별 형식 선택과 고정 `Append-only` 정책 표시.
- PDF→DOCX 변경 후 PATCH 저장, Version 1 ETag 갱신 PASS.
- 다시 변경 후 닫기에서 `저장/버리기/계속 편집` 보호 UI 표시.
- 내부 API 주소·DB 오류·Credential 원문 노출 0.
- Screenshot: `scripts/tests/web-final-ui-evidence/foundation-b11-output-version-1920x1080.png`
- Screenshot SHA-256: `0EBA46E82F1FD8E5BB559BDCC7A453C012202C6AB82A52C9F64C729E444F06DE`
- Browser tab closed, viewport reset, ports 14191/18491 listener 0, temporary logs 0.

## 자동 검증

- API focused: `12 passed, 7 skipped` (실제 PG는 위 별도 Gate로 PASS).
- Node/API/BFF/OpenAPI/UI: `63/63 PASS`.
- OpenAPI R1-M8: paths 81, operations 102, schemas 137, errors 31; SHA `393561181BEF75844F70AB75F23A73AC783A8405627265DE2B083D37A4A41973`.
- Web production build/TypeScript PASS, boundary 269/0.
- `git diff --check` PASS(기존 LF 경고만), staged 0.

# Foundation B4 Studio 근거 기반 보고서 actual Gate

- 실행 시각: `2026-08-15T05:11:12+09:00`
- Checkout: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`
- Branch/HEAD: `codex/user-auth-screen-split` / `dbe67f9bfe778b1ffa10b31f1e3e0faf807dd42b`
- 범위: 혼합 지식 답변에서 근거 기반 보고서를 생성하고 저장·상세·Version·Citation·검토·승인·Export까지 재진입 가능한 수직 계약

## TDD와 제품 교정

- RED: 저장된 산출물을 다시 열 때 Version/Citation/lifecycle 상세 GET 경로가 없고 Runtime `405`, BFF `405`, Web Adapter export 부재.
- GREEN: 기존 공개 Path `GET /api/v1/studio-outputs/{output_id}/versions`를 Repository→Service→Runtime→OpenAPI→same-origin BFF→Web Adapter→React Library에 연결.
- RED: `user_edit` 새 Version이 이전 EvidenceReference를 복사하지 않아 Citation 0건.
- GREEN: 이전 Version의 EvidenceReference를 새 Version에 append-only 복사하고 Citation ID·origin·locator를 보존.
- Actual PostgreSQL RED: ApprovalRequest와 OutputVersion 승인 전이가 같은 deterministic transition ID를 사용해 `STUDIO_STATE_INVALID`.
- GREEN: transition identity를 `approval-request`와 `output-version`으로 분리하고 동시 replay·lifecycle 의미를 유지.

## Actual PostgreSQL 15 Gate

- disposable DB: `daon_b4_studio_report_it_20260815`
- migration: `0001 -> 0016` PASS
- 제품 Repository 실제 실행:
  - 동일 Idempotency-Key concurrent revision은 Version 1건 생성 + replay 1건
  - 이전 Version Citation을 Version 2에 보존
  - Review → ApprovalRequest → Approval 상태 전이와 각 lifecycle ID 결속
  - `list_versions` 재진입 시 Version 2/1, Citation, lifecycle ID 복원
  - 승인 Version PDF Export bytes·SHA-256 검증
- 결과: `1 passed`, `B4_CURRENT_0016_PASS`, `B4_VERSION_CITATION_REVIEW_APPROVAL_EXPORT_PASS`
- cleanup: `B4_CLEANUP_REMAINING_0`, 기존 `local-postgres` running 유지

## Actual Browser same-origin Gate

- 격리 API: `127.0.0.1:18484`
- 격리 Web: `127.0.0.1:14184`
- viewport: `1920x1080`
- Browser 공개 요청은 `/bff/api/*` 상대 경로만 사용했고 내부 API 주소는 DOM에 노출되지 않았다.
- 실제 사용자 흐름:
  1. Raw Source 1건과 Daon 승인 지식 1건을 동시에 선택
  2. Question POST `200`, Raw+Daon Citation 2건 표시
  3. `근거 기반 보고서` 설정을 목적·독자·분량·구성·PDF·승인필수로 확인
  4. Generation POST `201`, `source_version_ids=[version-daon-b4,version-raw-b4]`
  5. `편집 새 Version` POST `201`, Version 2와 Citation 2건 보존
  6. 페이지 reload 후 Library 재진입, Version 2/1 이력·Daon section·Raw page locator 복원
  7. Review `201` → ApprovalRequest `201` → Step-up `201` → Approval `201`
  8. 추가 인증 비밀번호는 승인 직후 빈 값으로 소거
  9. PDF same-origin Export GET `200`, approved 상태 재진입 후 Export 버튼 활성
- Browser Console warn/error 0, 안전 Alert 0, internal URL/SQLSTATE/stack/password 반사 0.
- Browser full-page capture: `42,883 bytes`; Browser session 종료 후 격리 ports `14184/18484` listener 0.

## Fresh 자동 검증

- Studio PostgreSQL/Runtime focused: `22 passed, 1 skipped`
- Product/BFF/OpenAPI Node: `67 passed`
- API full: `397 passed, 28 skipped, 137 subtests passed`
- OpenAPI: `79 paths / 99 operations / 131 schemas / 31 errors`, SHA-256 `992923140334892018428F8F0975027E41E58EEB9177F793B543F24330916DEE`
- Web production build·TypeScript PASS, Web boundary `269 files / violations 0`
- 관련 Web source lint `2 files` PASS

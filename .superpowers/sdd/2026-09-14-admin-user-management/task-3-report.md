# Task 3 Web UI 구현 및 독립 리뷰 재작업 보고

COMPLETED | issue_id=R1-ADMIN-USER-MANAGEMENT-01 | 승인된 Task 3 구현 후 독립 리뷰의 Critical 1건과 Important 2건을 TDD로 수정했다. | 비밀번호 변경 CSRF provenance 전달, `/admin` 서버 측 session 권한 판정과 실제 403, 제한 session Notebook API 0회 동작 테스트를 추가했으며 기존 Task 3 UI를 유지했다. | focused UI/BFF 73/73 PASS, workspace lint PASS(16 files), Web production build PASS(471 files, violations 0, boundaryErrors 0), `git diff --check` PASS. | 실제 브라우저 Network/QA는 계획상 Task 5로 미검증이며 배포·backend/migration·조직 UI 파일 삭제는 수행하지 않았다. | main agent가 새 Task 3 재작업 commit을 검토하고 다음 승인 계획 단계로 진행한다.

## 수정 전후

- Critical CSRF: 수정 전에는 `POST /bff/api/auth/password/change`가 browser Origin만 검사하고 backend 필수 `x-daon-csrf-origin`, `x-daon-csrf-referer`를 보내지 않았다. 수정 후 logout과 동일한 `csrfProvenanceRequired` 계약을 사용하여 Origin과 same-origin Referer를 모두 검증하고, 검증된 정확한 값을 upstream에 전달한다. Referer 누락·cross-origin Referer·cross-origin Origin은 upstream 호출 없이 403이다.
- Important `/admin`: 수정 전에는 쿠키 존재만 확인한 뒤 일반 사용자에게도 HTTP 200 HTML을 반환하고 hydration 이후 이동했다. 수정 후 서버가 기존 BFF proxy와 session cookie를 사용해 `/api/v1/session` projection을 no-store로 조회한다. 미인증은 `/` redirect, `password_change_required`는 `/password-change` redirect, 일반 사용자는 Next `forbidden()`의 실제 HTTP 403이며, system admin만 console을 렌더링한다. 403에는 안전 안내와 Notebook 복귀 링크만 표시한다.
- Important 제한 session 경계: 수정 전에는 호출 순서가 코드 정규식으로만 확인됐다. 수정 후 Home과 selected Notebook component에 기본 동작을 보존하는 dependency seam을 두고, `password_change_required=true`일 때 Home `listNotebooks` 0회 및 selected `getNotebook`/`getNotebookContext` 각각 0회를 component 동작 spy로 검증한다.

## 생성·변경한 결과

- 생성: `apps/web/lib/server-admin-access.js`, `apps/web/app/forbidden.jsx`, `scripts/tests/admin-route-runtime.test.mjs`.
- 변경: `apps/web/lib/bff-api-proxy.js`, `apps/web/app/admin/page.jsx`, `apps/web/next.config.mjs`, Notebook Home/selected workspace, 관련 BFF/component/assembly tests.
- 보존: 기존 Task 3 사용자 관리/비밀번호 변경 화면, 조직 backend/migration/UI 파일, 범위 밖 변경은 되돌리거나 삭제하지 않았다.
- 임시물: `.admin-*`, `.restricted-*`, `.password-change-*` 테스트 bundle 디렉터리 잔류 0개를 확인했다.

## 테스트 결과

- RED 재현: CSRF 테스트는 Referer 누락 요청이 기존 구현에서 200을 반환해 `200 !== 403`으로 실패했다. 제한-session component tests는 dependency seam 적용 전 실패했고, `/admin` runtime test는 구현 전 완료되지 않았다.
- `node --test scripts/tests/notebook-api.test.mjs scripts/tests/admin-users-bff.test.mjs scripts/tests/admin-user-management-ui.test.mjs scripts/tests/admin-user-management-react.test.mjs scripts/tests/admin-route-runtime.test.mjs scripts/tests/phase-e-product-assembly.test.mjs scripts/tests/notebook-home-ui.test.mjs scripts/tests/notebook-home-react.test.mjs scripts/tests/api-bff-runtime.test.mjs scripts/tests/auth-pane.test.mjs` → 73/73 PASS.
- 실제 Next route 검증 포함: no-cookie `/admin` 307 및 `/` Location, member session `/admin` 403 및 관리 사용자 데이터 미포함, system-admin session `/admin` 200.
- CSRF mock upstream 검증 포함: exact `x-daon-csrf-origin=https://app.example.com`, exact `x-daon-csrf-referer=https://app.example.com/password-change?required=1`; 세 음성 요청은 upstream 0회.
- `npm run lint:workspace` → PASS, `workspace lint passed: 16 files`.
- `npm run build --workspace @daon-user/web` → PASS, Next.js 16.3.3 compile/type/page generation 성공, `/admin` dynamic route, product boundary `scannedFiles=471`, `violations=[]`, `boundaryErrors=[]`.
- `git diff --check` → PASS(출력 없음, Git global ignore 접근 warning만 존재).

## 미해결 사항

- 실제 브라우저 클릭, BFCache/history 체감 동작 및 Network에서 same-origin URL 확인은 승인 계획의 Task 5 범위로 남겼다.
- Next 16의 실제 403 응답을 위해 공식 experimental `authInterrupts`를 활성화했다. production build와 실제 Next HTTP 테스트는 통과했으나 브라우저 QA는 위와 같이 Task 5에서 수행해야 한다.
- push, PR, merge, 배포는 이번 재작업 범위가 아니다.

## 다음으로 필요한 판단

- main agent가 재작업 commit과 위 검증 근거를 독립 리뷰하고, 승인 계획의 다음 Task 진행 여부를 판단한다.

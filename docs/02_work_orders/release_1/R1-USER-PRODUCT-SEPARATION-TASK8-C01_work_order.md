# R1 사용자 제품 분리 Task 8 Clean Build 보정 작업지시서

## 1. 판정·승인·기준선

- Work Order ID: `R1-USER-PRODUCT-SEPARATION-TASK8-C01`; Issue ID는 기존 `R1-USER-PRODUCT-SEPARATION-TASK8-01-I001`을 유지한다.
- 상태: `READY` · 2026-08-12. 신산님이 Task 8 `FAILURE_REPORT`의 정확한 두 Route 제한 복원과 Product Boundary 보정을 승인했다.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; 현재 Branch·HEAD·origin과 Dirty 상태를 착수 시 다시 기록한다.
- 동일 어울2 한 명만 Writer다. 어울1은 결과 수집 전 제품 코드를 수정하지 않는다.
- 착수 전 `AGENTS.md`, Task 8 원 작업지시·Progress·Completion, 승인 제품 분리 설계·계획, Stage A Completion을 EOF까지 읽고 적용 조항과 SHA-256을 Progress에 기록한다.

## 2. 목표와 보존 경계

- clean checkout과 현재 보호 Dirty checkout에서 Account·Organization 사용자 Route가 동일하게 안전한 제품 화면으로 존재하도록 한다.
- 두 Route는 Evidence Hub·Prototype·Mock·`prototype_fixture`·`deferred_actual`을 import 또는 표시하지 않고 Network·Adapter·Session·상태변경을 수행하지 않는 `RESOURCE_UNAVAILABLE` Safe 화면이어야 한다.
- Product Boundary 검증기는 두 Route가 없거나 Symlink이거나 읽을 수 없으면 fail-close하고, clean Web Build 산출물까지 검사해야 한다.
- 기존 사용자 삭제 27건 중 아래 두 경로만 HEAD 원본에서 제한 복원 후 보정한다. 종료 시 나머지 삭제 25건, Cargo 동일 Blob 표시, Native Evidence와 기존 미추적 문서 3건을 그대로 보존한다.
  - `apps/web/app/settings/account/page.jsx`
  - `apps/web/app/settings/organization/page.jsx`
- 서버·배포·Container·DB·Migration·NSIS·브라우저·Credential·Commit·Push·PR은 본 보정 범위에서 변경하거나 실행하지 않는다.

## 3. 허용 파일

- Modify:
  - `apps/web/app/settings/account/page.jsx`
  - `apps/web/app/settings/organization/page.jsx`
  - `scripts/verify-product-ui-boundary.mjs`
  - `scripts/tests/product-ui-boundary.test.mjs`
- Create/append:
  - `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-TASK8-C01_progress.md`
  - `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-TASK8-C01_completion_report.md`
- 본 작업지시·프롬프트 외 다른 Source·Test·Config·Lock·Evidence 파일 수정과 Stage를 금지한다.

## 4. TDD 구현 순서

1. 기준선과 두 Route의 HEAD Blob을 기록하고, 두 파일이 삭제된 현재 상태에서 Default Web Product Boundary가 이를 누락하는 행동 RED를 추가한다.
2. 검증기에 두 Route를 exact 필수 Product Entry로 추가한다. Missing·Symlink·Unreadable은 구조 오류로 fail-close하고, import graph와 build artifact 검사는 기존 동작을 보존한다.
3. 승인된 두 경로만 HEAD에서 복원한 뒤 Prototype UI import를 제거하고, 네트워크·Fixture가 없는 최소 Safe 사용자 화면으로 보정한다. 화면 제목은 Account/Organization 목적을 유지하고 오류 코드는 승인된 `RESOURCE_UNAVAILABLE`만 사용한다.
4. Route source와 실제 build artifact에 금지 Token·Network 호출이 0임을 테스트한다. 다른 Route·공용 UI·서버 전용 BFF 예외를 확장하지 않는다.
5. clean checkout 대표 Fixture와 현재 실제 Tree 모두에서 검증기가 통과하는지 확인한다.

## 5. 필수 검증

```powershell
node --test scripts/tests/product-ui-boundary.test.mjs
npm run build --workspace @daon-user/web
npm run verify:product-ui-boundary
npm run verify:workspace
git diff --check
```

- Web Build 후 `.next/static`, `.next/server/app`, `.next/server/chunks`를 포함한 Product Gate의 scanned files·violations·boundaryErrors 수치를 기록한다.
- 두 Route의 실제 React 렌더 또는 동등한 실행 기반 검증에서 Adapter·fetch·Tauri invoke 0을 증명한다. Source regex만으로 완료를 주장하지 않는다.
- `git diff -- <두 Route>`로 복원·보정 Diff를 기록하고, 나머지 삭제 25건·staged 0·Cargo HEAD Blob 동일·기존 미추적 문서 보존을 확인한다.
- Sandbox/OneDrive 권한 오류는 제품 실패와 분리하고 동일 명령의 승인 실행만 허용한다. 기능 실패를 우회하지 않는다.

## 6. 결과 계약

- `COMPLETED`는 RED→GREEN, clean Web Build, Product Gate 0 violation/0 boundary error, 실행 기반 Network 0, 보호 Dirty 보존이 모두 증거로 충족될 때만 허용한다.
- 제품 결함이 남으면 기존 Issue의 정식 `FAILURE_REPORT`, 환경·권한 문제면 `BLOCKED`, 중단이면 `INCOMPLETE`로 제출한다.
- 결과 형식: `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`.
- 완료 후 어울1의 독립 Diff 검토와 별도 PR 승인 전 Commit·Push·배포·Task 8 재개를 하지 않는다.

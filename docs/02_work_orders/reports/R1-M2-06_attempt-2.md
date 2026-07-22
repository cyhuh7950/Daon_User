BLOCKED | R1-M2-06-I001 | C01 권한 우회·Route 정본·Browser 증거 보정과 검증 완료 | AccountSecurity Model·Pane·Test·Adapter 계약·Browser JSON·PNG 4개·Manifest·Progress | 전용 20/20·전체 97/97·Lint·Production Build·Browser PASS, 공통 Gate는 production-dependency-audit 1건만 FAIL | 2026-07-22 신규 npm audit 기준에서 Next 16.2.10의 Sharp 0.34.5 High·PostCSS 8.4.31 Moderate 취약점이 보고됐고 Next 직접 의존 Fix가 제공되지 않음 | 어울1이 R1-M2-06 금지 범위 밖 Dependency·Lockfile 보정 작업을 별도 판단하고 Gate 재실행 여부 결정

# R1-M2-06 개발 결과보고 — Attempt 2

## 판정

`BLOCKED` · C01 제품 코드·화면 증거 보정은 완료됐으나 공통 품질 Gate의 신규 Production Dependency Audit을 통과하지 못했다.

이는 `FAILURE_REPORT`가 아니다. 권한 우회·Route·Browser 증거 결함은 승인 경계 안에서 모두 수정·검증됐고, 남은 항목은 이번 작업지시서가 명시적으로 금지한 Dependency·Lockfile 변경 판단이다.

## 수행한 작업

- 민감 Action Registry를 단일 정본으로 만들고 등록되지 않은 Action을 `STEP_UP_ACTION_NOT_ALLOWED`로 발급 전에 차단했다.
- Step-up 발급 전과 소비 직전에 Actor·MembershipRole·세부 권한·Tenant·Workspace·Policy Version을 재검사한다.
- 발급 뒤 권한 회수·Membership/Scope 변경은 Authorization을 소비하지 않고 `CURRENT_ACCESS_DENIED`로 종료하며 Domain·영역 이동 상태를 바꾸지 않는다.
- 영역 이동의 권한 검사·명시 승인·전송 Preview·버전/Audit 단계에 현재 권한과 동일 Step-up Scope Guard를 적용했다.
- 현재 화면에 따라 Account/Organization URL·Route ID·Screen ID·제목을 동적으로 투영하고 왕복·History 복원 상태를 검증했다.
- 최종 Production Browser 세션에서 1920·1200·800·500 화면을 다시 촬영하고 Browser JSON의 Pixel Dimension·표시 문자열·클릭 순서와 직접 대조했다.

## 변경 결과

| 구분 | 파일 |
| --- | --- |
| Domain | `packages/ui/src/account-security-model.js` |
| UI | `packages/ui/src/account-security-pane.jsx`, `packages/ui/src/index.js`, `packages/ui/src/workspace.css` |
| Route | `apps/web/app/settings/account/page.jsx`, `apps/web/app/settings/organization/page.jsx` |
| Test | `scripts/tests/account-security.test.mjs` |
| Architecture | `docs/01_architecture/account_security_prototype_adapter_contract.md` |
| Evidence | `docs/03_evidence/release_1/R1-M2-06/` |
| Recovery record | `docs/04_test_reports/release_1/R1-M2-06_progress.md` |

보호 Dirty `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`, `violations.json`은 수정·복원·Stage하지 않았다. 공통 Gate가 갱신한 M1-05 `quality-gate-result.json`, `quality-gate-summary.md`는 실패 증거를 판독한 뒤 HEAD 내용으로 복원해 작업 범위에 포함하지 않았다.

## 검증 근거

| 검증 | 결과 |
| --- | --- |
| C01 유효 RED | 기존 16/16 PASS 뒤 신규 4/4 FAIL — 권한 발급/소비 우회, 정적 Route Props, Browser 직접 증거 부족 |
| 전용 Test | `node --test scripts/tests/account-security.test.mjs` · 20/20 PASS |
| 전체 회귀 | Account + Studio + Workspace + Source + Run + Foundation · 97/97 PASS |
| Lint | `npm run lint:workspace` · 11 files PASS |
| Production Build | `npm run build --workspace @daon-user/web` · Exit 0; `/settings/account`, `/settings/organization` 생성 |
| Browser | 새 C01 Production Build/Session, PNG 4개 직접 검수, Console warning/error 0/0, JSON/실제 Pixel Dimension 일치 |
| 공통 Gate | 7개 Category 중 lint·type·unit·contract·build·independence PASS, security의 `production-dependency-audit`만 FAIL, Overall FAIL/Exit 1 |
| Diff 경계 | Dependency·Lockfile·Toolchain·CI 변경 0건, 보호 Dirty 2개 보존 |

## 차단 근거

`npm audit --omit=dev --audit-level=high --json`의 2026-07-22 결과:

- `next@16.2.10` 직접 Production Dependency
- `sharp@0.34.5`: High, `GHSA-f88m-g3jw-g9cj`, `<0.35.0`
- `postcss@8.4.31`: Moderate, `GHSA-qx2v-qp2m-jg93`, `<8.5.10`
- Audit 집계: High 2, Moderate 1, Critical 0
- `next`에 대한 `fixAvailable=false`

R1-M2-06은 Dependency·Lockfile·Toolchain·CI 설정 변경을 금지한다. 따라서 이번 C01 안에서 Override·직접 의존 추가·Next 변경을 임의 수행하지 않았다. 공통 Gate PASS는 완료 조건이므로 `COMPLETED` 또는 `HANDOFF_READY`로 선언하지 않는다.

Gate 실행 wrapper는 300초 도구 제한에 도달했지만 하위 `verify-quality-gate.mjs` 프로세스가 계속 실행되어 최종 결과 파일을 생성했다. 이는 Gate 실패 원인이 아니며 실제 실패는 위 Production Dependency Audit 한 건이다.

## 남은 위험과 필요한 판단

- 권한·Route·Browser C01 기능 결함은 자동·시각 검증 기준으로 해소됐다.
- 실제 Auth/API/DB/MFA/Session·Key 철회/Egress는 원 계획대로 M3~M8 후속이다.
- 어울1은 Dependency·Lockfile을 소유하는 별도 보안 보정 Work Order 또는 기준선 갱신 필요성을 판단해야 한다.
- 보정 후 동일 공통 Gate를 재실행해 PASS를 확보하기 전에는 R1-M2-06을 기술 완료로 수락할 수 없다.
- Commit·Push·ysna-server·PR·Merge는 수행하지 않았다.

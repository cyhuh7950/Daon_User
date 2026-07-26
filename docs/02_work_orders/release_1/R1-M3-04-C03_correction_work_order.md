# R1-M3-04-C03 수정 작업지시서 — CI 역사 Blob 검증 이력 복구

## 1. 판정

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M3-04` · issue `R1-M3-04-I001` |
| 검토 결함 | `R1-M3-04-C03-CI-HISTORY` |
| 판정 | 중대 미진 · 별도 수정 작업지시 |
| 발견 주체 | 어울1 PR #18 Quality Gate 검토 |
| 실패보고 누적 | `0` · CI 실패는 어울2의 정식 `FAILURE_REPORT`가 아님 |
| 실패 Run | GitHub Actions `30203630301` · PR Merge SHA `6e2c58fae87d3c125455e30acd6aaba65a4efe3c` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-04_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-04_attempt-4.md` |

판단 이유: PR #18의 공통 Gate에서 Mobile Workspace 5개, Build, Contract, Security, Independence와 Desktop Rust Type은 통과했지만 `desktop-shell-unit`만 시작 후 약 0.29초에 Exit 1이었다. 해당 Test는 고정 Successor Commit `8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa`의 `package.json`·`package-lock.json` Blob을 `git show`로 읽는다. Workflow의 `actions/checkout@v5` 단계에는 역사 Commit을 가져오는 계약이 없어 PR의 얕은 Checkout에서는 고정 Commit 객체가 존재하지 않을 수 있다. Local Full History와 ysna-server 검증은 통과했으므로 Mobile 기능 결함으로 분류하지 않는다.

## 2. 수정 계약

- GitHub Actions의 불변 Candidate Checkout이 C01에서 승인한 고정 Origin·Successor Commit과 Ancestor 관계를 검증할 수 있도록 필요한 Git 이력을 명시적으로 확보한다.
- 가장 단순하고 Fail-close인 기본안은 Checkout 단계의 `fetch-depth: 0`이다. 더 좁은 이력 Fetch를 택하면 고정 Commit 두 개와 Ancestor 관계 검증이 모두 재현됨을 Test와 근거로 입증한다.
- Checkout Action Major, 승인 Toolchain, Tauri 선행 패키지, Gate 순서, Evidence Upload와 Fallback 계약은 변경하지 않는다.
- 역사 Blob 검증 실패를 Skip·조건부 PASS·현재 Checkout 대체로 우회하지 않는다.
- C01의 고정 Commit·Hash·Byte·Ancestor 검증과 R1-M3-04 Mobile 기능 계약을 변경하지 않는다.

## 3. TDD·변경 범위

먼저 기존 Workflow 계약 Test에 다음 RED를 추가한다.

1. Checkout 단계가 역사 Blob·Ancestor 검증에 필요한 이력 깊이를 명시하지 않으면 FAIL한다.
2. 승인된 Action Major와 기존 Step 순서·권한·Fallback·Upload 계약은 그대로 PASS해야 한다.
3. 고정 Successor Blob과 현재 Checkout 핵심 Pin을 분리한 C01 Test는 그대로 PASS해야 한다.

허용 변경:

- `.github/workflows/release-1-quality-gate.yml`
- 기존 Workflow 계약 Test인 `scripts/tests/product-foundation.test.mjs` 또는 `scripts/tests/quality-gate.test.mjs`
- R1-M3-04 Progress, Attempt 4, Evidence 5종·Manifest의 정확한 Source·Hash·검증 상태

금지 변경:

- Mobile·Desktop·Web·Local Service Production Source
- `scripts/lib/predecessor-evidence-reconciliation.mjs`와 고정 역사 Commit·Hash·Byte 계약
- Mobile Workspace 명령, Quality Gate Capability, 의존성·Lockfile·Toolchain Pin
- Test Skip·조건부 PASS·검사 삭제·고정 Commit을 현재 Checkout으로 대체
- Commit·Push·PR·Merge·서버·GUI·Native Project

## 4. 완료 증거

- Workflow 계약 Test RED→GREEN
- C01 지정 3 Test와 Workflow·Quality Gate 관련 Test PASS
- 전체 Node 회귀, Mobile Workspace 5개, Toolchain, Audit, Independence PASS
- 최종 R1-M3-04 공통 7범주 Gate Overall PASS·Failures 0
- Workflow JSON Parse, `git diff --check`, 관련 없는 Diff·삭제 0
- Evidence Manifest에 C03 변경 Source를 포함하고 Source/Evidence Hash·Byte mismatch 0
- Attempt 4에 PR 실패 Run과 수정 근거, Local 검증, GitHub 재검증은 어울1 후속임을 구분

정식 결과 형식:

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

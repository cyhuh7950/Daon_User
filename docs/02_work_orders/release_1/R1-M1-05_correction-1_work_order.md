# 수정 작업지시서 `R1-M1-05-C1`

## 1. 판정

`REWORK` · 원 Work Order `R1-M1-05` · issue_id `R1-M1-05-I001` 유지 · Attempt 1 내부 Correction 1

## 2. 판단 이유

어울2의 S5 `HANDOFF_READY` 결과는 로컬 7범주 Gate와 허용 Diff 검사를 통과했으나, 어울1 검토에서 Merge Gate 신뢰성을 깨는 중대 미진 2건이 확인됐다.

1. `.github/workflows/release-1-quality-gate.yml`은 Node만 설정하고 `toolchain-versions.json`이 고정한 `npm 11.12.1`, `corepack 0.35.0`, `uv 0.11.2`를 CI Runner에 준비하지 않는다. 공통 Gate의 `verify:toolchain`은 이 세 Runtime을 직접 검사하므로 실제 GitHub CI가 로컬과 다른 환경에서 실패할 수 있다.
2. `validatePolicy()`는 7개 범주 배열 존재만 확인하고, 항상 필수인 Check ID·범주·명령과 승인 Component Matrix의 완전성·중복·Foundation 상태를 강제하지 않는다. Policy 항목 삭제 또는 변형이 Schema 오류 없이 조용한 완화로 이어질 수 있다.

이 두 항목은 사소한 문구 보완이 아니라 “CI와 로컬 동일 계약”과 “필수 검사 실패 시 Merge 차단” 완료조건을 직접 위반하므로 Commit·Push 전 재작업한다. 기존 정상 구현 전체를 다시 열지 않고 아래 두 결함과 관련 Test·Evidence만 보완한다.

## 3. 조치

### 3.1 CI 정확 Toolchain 준비

- Workflow에서 `.node-version`의 Node를 설정한 뒤, `npm ci` 전에 다음 승인 Runtime을 정확 버전으로 준비하고 버전을 출력·검증한다.
  - `npm 11.12.1`
  - `corepack 0.35.0`
  - `uv 0.11.2`
- 버전 값은 Workflow 여러 곳에 임의 중복하지 말고 `toolchain-versions.json` 또는 승인 Pin 파일과 정합을 기계 검증한다.
- Ubuntu GitHub Runner에서 재현 가능한 공식 설치 경로를 사용한다. `uv`는 정확 버전을 지정하고, npm/corepack은 설치 후 실제 `--version`을 Gate 전에 확인한다.
- Workflow Test는 Setup 단계가 존재한다는 문자열 확인만 하지 말고, 정확 세 버전과 실행 순서가 `npm ci` 및 공통 Gate보다 앞서는지 검증한다.
- 로컬 Runner 계약은 변경하지 않는다. CI만 검사를 생략하거나 `continue-on-error`로 완화하지 않는다.

### 3.2 Policy Fail-close Schema

- 승인 Component ID 8개가 정확히 한 번씩 존재해야 한다: `apps/web`, `apps/desktop`, `apps/mobile`, `packages/ui`, `packages/contracts`, `packages/design-tokens`, `services/api`, `services/local-service`.
- `foundation_status`는 정확히 `NOT_APPLICABLE_FOUNDATION_ONLY`여야 한다.
- 항상 필수 Check 4개가 정확히 한 번씩 존재하고 승인 범주·종류와 일치해야 한다:
  - `quality-gate-runner-tests` → `unit`
  - `toolchain-baseline` → `build`
  - `production-dependency-audit` → `security`, `kind=npm_audit`
  - `repository-independence` → `independence`
- 각 필수 Check의 `command`는 비어 있지 않은 문자열 배열이어야 하고, 구성요소 Capability의 명령이 있으면 같은 Schema를 만족해야 한다.
- Component Root와 Manifest가 실제 저장소에 없거나 Manifest가 Component Root 밖을 가리키면 성공/N/A가 아니라 Policy/계약 오류로 Exit 2가 되어야 한다.
- 중복 ID, 필수 Check 삭제·범주 변경, Component 삭제, 잘못된 Foundation 상태, 빈 명령, Manifest 부재를 각각 음성 Test로 고정한다.
- 기존 Runtime Source 등장·명령 실패·Audit Network·Secret Masking·Workflow 계약 Test는 유지한다.

### 3.3 범위와 금지

- 변경 허용: 원 작업지시서 허용 경로 중 이번 결함과 직접 관련된 `quality-gate-policy.json`, `.github/workflows/release-1-quality-gate.yml`, `scripts/lib/quality-gate.mjs`, `scripts/tests/quality-gate.test.mjs`, 필요 시 `scripts/verify-quality-gate.mjs`, `docs/01_architecture/ci_quality_gate_contract.md`, 진행 기록, 로컬 Evidence.
- `package.json`의 이미 추가된 `verify:quality-gate` Script는 유지하고 다른 항목을 변경하지 않는다.
- Lockfile, Toolchain Pin, 제품 Source, 승인 정본, 선행 Evidence, Work Order/Prompt, Git Commit·Push·PR, 서버 배포는 변경·수행하지 않는다.
- 다른 작업자의 변경을 되돌리거나 정리하지 않는다.

## 4. 단계와 완료조건

| 단계 | 작업 | 완료조건 |
| --- | --- | --- |
| C1-S0 | 현재 HANDOFF_READY Diff·진행 기록·두 결함 재현 | 기존 파일 보존, 음성 Test Red 확인 |
| C1-S1 | Workflow Toolchain 준비·정확 버전/순서 Test | npm·corepack·uv Pin과 순서 Test PASS |
| C1-S2 | Policy Schema Fail-close와 음성 Test | 6개 이상 신규 Schema 음성 Test가 Exit 2 증명 |
| C1-S3 | 전체 비설치 회귀 및 공통 Gate 재실행 | Syntax, 전체 Test, Toolchain, 독립성, Audit, 7범주 Gate PASS |
| C1-S4 | Evidence·진행 기록 갱신 후 HANDOFF_READY | Hash·Diff·삭제·허용 경로 검사 PASS, 쓰기 중지 |

- 설치는 이미 승인 Lockfile의 격리 Offline 성공 증거가 있으므로 동일 `npm ci`를 반복하지 않는다. Lockfile 변경이 생긴 경우에만 중지하고 원인을 보고한다.
- `docs/04_test_reports/release_1/R1-M1-05_progress.md`에 Correction 착수, Red, 각 수정, Green, 오류·복구, 최종 `HANDOFF_READY`를 Append한다.
- 완료보고은 최종 `COMPLETED`가 아니라 다시 `HANDOFF_READY` 중간 보고다. 어울1이 검토·Commit·Push한 불변 SHA를 전달한 뒤에만 S6 서버 검증을 재개한다.

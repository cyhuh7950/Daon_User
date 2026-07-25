# R1-M3-02-FIX-09 수정 작업지시서

- 원 작업: `R1-M3-02`
- 선행 수정: `R1-M3-02-FIX-01`~`FIX-08`
- Issue ID: `R1-M3-02-DESKTOP-TYPE-FRONTEND-PREREQUISITE`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-25
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정

PR #16 Run `30159805607`의 명시적 `Verify desktop Rust type prerequisites` Step에서 다음 원인이 확정됐다.

```text
error: proc macro panicked
 --> src/lib.rs:4:14
  |
4 |         .run(tauri::generate_context!())
  |              ^^^^^^^^^^^^^^^^^^^^^^^^^^
  |
  = help: message: The `frontendDist` configuration is set to `"../dist"` but this path doesn't exist
```

Ubuntu Package나 Rust Toolchain의 문제가 아니다. clean checkout의 `npm ci` 직후에는 `apps/desktop/dist`가 없는데, Tauri `generate_context!()`가 Rust Type Check 시 `frontendDist`의 존재를 검증한다. 서버 검증은 선행 Desktop Build가 `dist`를 만든 뒤 Type Check를 실행해 통과했다. 따라서 공통 검증 명령의 선행조건을 명시적으로 보강해야 한다.

정식 실패보고가 아니며 유효한 실패 횟수는 `0회`다.

## 2. 목표와 구현 계약

- Root `.npmrc`의 승인 보안 계약 `ignore-scripts=true`와 Toolchain Baseline 강제를 그대로 유지한다. npm `pre*` Lifecycle은 해결책으로 사용하지 않는다.
- Root `package.json`의 호출 이름 `verify:desktop-type`은 유지하되 Script 본문을 정확히 `npm run verify:desktop-build && node scripts/run-isolated-desktop-cargo.mjs check`로 변경한다.
- 기존 Cargo Wrapper의 구현과 독립 실행 명령은 변경하지 않는다.
- `npm run verify:desktop-type` 한 명령이 clean checkout에서도 Desktop Frontend Build를 먼저 완료한 다음 Rust Type Check를 수행해야 한다.
- Workflow의 명시적 진단 Step과 공통 Quality Gate는 계속 같은 `npm run verify:desktop-type` 명령을 사용하며 Skip·허용 실패·Gate 완화를 추가하지 않는다.
- 생성된 `apps/desktop/dist`는 Build 산출물일 뿐 Git 추적 대상으로 추가하지 않으며 최종 종료 전에 제거한다.
- Dependency·Lockfile·Tauri 설정·제품 Runtime·UI·API·데이터 계약은 변경하지 않는다.

## 3. TDD·구현 절차

1. Root Script 계약 테스트를 먼저 추가해 clean-checkout 선행 Build 체인 부재 RED를 확인한다.
2. `verify:desktop-type`의 정확한 명시적 Build→Cargo Check 명령과 `.npmrc`의 `ignore-scripts=true` 불변을 테스트로 고정한다.
3. `package.json`의 기존 Script 한 항목만 최소 변경해 계약 테스트 GREEN을 확인한다. 앞선 `preverify:desktop-type` 시도와 그 전용 기대는 제거한다.
4. `apps/desktop/dist`가 없는 상태를 확인한 뒤 `npm run verify:desktop-type`을 실행한다.
5. Frontend Build가 먼저 성공하고 이어서 Cargo Check가 통과하는 원문 증거를 남긴다.
6. Generator·Manifest·Progress·Attempt-2에 FIX-09와 세 번째 CI 원인, clean-checkout 선행조건을 정합화한다.
7. 최종 검증 뒤 생성된 `dist`, Cargo `gen/target`, 임시 Target과 Process를 정리한다.

## 4. 검증

- 전용 RED→GREEN 계약 테스트와 `ignore-scripts=true` 보존 확인
- clean 상태에서 `apps/desktop/dist` 부재 확인 → `npm run verify:desktop-type` PASS → `dist` 생성 확인
- Independence 위반 0
- 전체 테스트, Lint, Web/Desktop Build, Desktop Rust Type, Audit 0, 로컬 7범주 Gate PASS
- JSON/Hash·Byte, `git diff --check`, R1-M1-05 기준선, 잔존물 0
- `package-lock.json`과 제품 Source/설정의 불변 확인

## 5. 보호 범위

- 기존 Cargo Wrapper의 fail-close·격리·보존 계약과 `.npmrc` 보안 기준을 변경하지 않는다.
- `dist` 커밋, CI 전용 Skip, 허용 실패, Gate 순서 우회, Dependency 추가를 금지한다.
- 화면/App, Commit, Push, PR 조작, 서버 명령·배포를 수행하지 않는다.
- 다른 작업자와 같은 파일을 병렬 수정하지 않는다.

## 6. 종료 조건

- Progress와 Attempt-2를 단계마다 갱신한다.
- 종료 보고는 `status | issue_id | 수행한 작업 | 생성·변경 결과 | 테스트 결과 | 미해결 사항 | 다음 판단` 형식을 사용한다.

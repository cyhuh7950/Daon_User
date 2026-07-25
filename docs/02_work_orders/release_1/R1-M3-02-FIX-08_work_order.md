# R1-M3-02-FIX-08 수정 작업지시서

- 원 작업: `R1-M3-02`
- 선행 수정: `R1-M3-02-FIX-01`~`FIX-07`
- Issue ID: `R1-M3-02-CI-RUST-TYPE-DIAGNOSTIC`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-25
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정

PR #16 Run `30159199959`에서 Ubuntu Tauri 필수 패키지 설치와 Independence는 통과했다. 공통 Gate는 `type/desktop-rust-type` 1건만 실패했지만 Gate Runner가 원문 stdout/stderr를 증거에 저장하지 않는 보안 계약 때문에 세부 Cargo 오류가 없다. 근거 없이 Package를 더 추가하거나 Gate를 완화하지 않는다.

정식 실패보고가 아니며 유효한 실패 횟수는 `0회`다.

## 2. 목표와 구현 계약

- `npm ci` 뒤, 공통 Quality Gate 앞에 `Verify desktop Rust type prerequisites` Fail-fast Step을 추가한다.
- Step은 Gate와 정확히 같은 `npm run verify:desktop-type` 명령을 실행하고 `continue-on-error`를 사용하지 않는다.
- 통과하면 기존 공통 Gate가 그대로 다시 검증한다. 실패하면 GitHub Job Log에 Cargo 오류가 직접 남아 다음 기술 판단의 근거가 된다.
- CI Fallback Evidence에는 이 Step Outcome을 별도 필드로 전달해 현재 Run의 진단 경계를 정직하게 기록한다.
- Quality Gate 정책·명령·필수 범주와 보안상 Artifact 비밀정보 제외 계약은 변경하지 않는다.

## 3. TDD·증거

1. Workflow 계약 테스트를 먼저 추가해 진단 Step 부재 RED를 확인한다.
2. Step ID, 정확 명령, npm-ci 이후·quality-gate 이전 순서, Fail-close를 고정한다.
3. Fallback 환경 변수 전달을 테스트한다.
4. 최소 Workflow 변경으로 GREEN을 만든다.
5. Generator·Manifest·Progress·Attempt-2에 FIX-08과 두 번째 CI 결과를 정합화한다.

## 4. 검증

- 전용 RED→GREEN
- Independence 위반 0
- 전체 테스트, Lint, Web/Desktop Build, Desktop Rust Type, Audit 0, 로컬 7범주 Gate PASS
- JSON/Hash·Byte, `git diff --check`, R1-M1-05 기준선, 잔존물 0

## 5. 보호 범위

- 제품 Runtime·UI·API·데이터·보안·Dependency·Lockfile·Tauri 설정을 변경하지 않는다.
- CI 전용 Skip, 허용 실패, Gate 완화, 비밀정보 원문 Artifact 저장을 금지한다.
- 화면/App, Commit, Push, PR 조작, 서버 명령·배포를 수행하지 않는다.

## 6. 종료 조건

- Progress와 Attempt-2를 갱신한다.
- 종료 보고는 `status | issue_id | 수행한 작업 | 생성·변경 결과 | 테스트 결과 | 미해결 사항 | 다음 판단` 형식을 사용한다.


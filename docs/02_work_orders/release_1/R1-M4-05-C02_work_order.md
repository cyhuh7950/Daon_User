# R1-M4-05-C02 Quality Gate 실행시간 정합 보완 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-05-C02`.
- Branch `codex/r1-m4-05`, 기준 HEAD `38ac37b4b831c3e7f900be6122f934789d611588`, 시작 Clean.
- PR #25 Quality Run `30412602867`의 실제 cancelled 증거와 R1-M4-05 Runtime capability 추가를 적용한다.
- 어울2가 이 Worktree와 범위의 유일한 Writer다. PR·CI 재실행·Merge는 어울1 소유다.

## 판정과 단일 목표

- 판정: `CI_EXECUTION_CONTRACT_MISMATCH / CORRECTION_REQUIRED`.
- 증거: Workflow 준비 완료 후 `Run common quality gate`가 28분 9초 실행되던 중 Job 총 30분 제한에 도달해 `cancelled`; 실패 로그·제품 Assertion 실패 없음.
- 목표: 실제 API·Next Process 검증이 추가된 Release 1 전체 Gate가 정상 종료할 수 있도록 Job timeout을 60분으로 정합화하고 기존 fail-close·evidence upload 계약을 보존한다.

## 허용·제외 범위

- 허용: `.github/workflows/release-1-quality-gate.yml`의 Job timeout 값, 해당 Workflow 구조 검증 테스트/문서, C02 진행·완료보고.
- 제외: Quality 검사 삭제·병렬화·skip·조건 완화, 제품 코드·BFF·API·iOS 변경, iOS Workflow 변경, dependency·Lockfile 변경.
- `Run common quality gate`, current-run evidence fail-close, upload, immutable checkout와 모든 기존 Step을 그대로 보존한다.

## 구현·검증 계약

- `release-1-quality-gate` Job의 `timeout-minutes`만 `60`으로 변경한다. 개별 검사 timeout이나 실패 판정을 완화하지 않는다.
- Workflow 정적/JSON·YAML 구문, immutable checkout, Step ID·조건·evidence fallback/upload가 그대로인지 테스트한다.
- Quality runner unit, workflow 관련 테스트, diff/secret/independence를 실행한다.
- 로컬 전체 Quality Gate를 반복하지 않는다. 실제 CI 60분 계약은 어울1이 PR #25의 새 Push Run으로 검증한다.
- iOS Run `30412602874` 실패는 변경 범위 밖 기존 Settings UI 변동성으로 기록하고 iOS 파일을 수정하지 않는다.

## 진행·보고

`docs/04_test_reports/release_1/R1-M4-05-C02_progress.md`에 착수, RED/근거, 변경, 검증, 종료 직전을 기록한다. 완료 후 단일 보완 Commit을 Push하고 Local/Remote SHA·Clean을 보고한다.

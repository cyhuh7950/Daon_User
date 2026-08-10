# R1-M5-07 Windows 최소 파일 복원 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WINDOWS-RESTORE-01` |
| issue_id | `R1-M5-07-WINDOWS-RESTORE-01-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 승인 | 신산님 2026-08-10 두 경로 제한적 복원 승인 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 기준 Commit | `b3503d6` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-RESTORE-01_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-RESTORE-01_completion_report.md` |

## 2. 승인 기준

다음 문서를 EOF까지 읽고 Hash·승인·적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.9
- `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.7
- `docs/04_test_reports/release_1_test_plan.md` 0.9
- `docs/02_work_orders/release_1/R1-M5-07_work_order.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-SCOPE-01_completion_report.md`
- `docs/03_evidence/release_1/R1-M5-07-WINDOWS-SCOPE-01/scope-analysis.md`

## 3. 목표

신산님이 승인한 다음 두 파일만 현재 `master` HEAD의 원본 Blob으로 복원하고 무결성과 관련 회귀를 확인한다.

- `apps/desktop/src-tauri/src/bin/local-service-lifecycle-host.rs`
- `apps/desktop/src-tauri/tests/fixtures/local-service-error-fixture.mjs`

나머지 삭제 31건과 미추적 사용자 문서 3건은 그대로 보존한다. 이 작업은 Windows Recovery Adapter 구현이나 제품 완료를 포함하지 않는다.

## 4. 실행 순서

1. 착수 시 HEAD·origin/master·Branch·Dirty를 기록한다.
2. 두 경로의 현재 부재와 HEAD Blob을 다시 확인한다.
3. 두 경로만 `git restore --source=HEAD -- <exact paths>`로 복원한다.
4. 다음 예상 Blob Hash와 `git hash-object`를 대조한다.
   - lifecycle host: `df932af6d5cb76686c78e5288a506139aaf9a3ed`
   - error fixture: `6f098b17d4292a0932d087559b24c518de5a7bdf`
5. 나머지 tracked 삭제가 정확히 31건, 기존 미추적 사용자 문서가 3건인지 확인한다.
6. 관련 정적·단위 회귀를 실행한다.
   - `node --test scripts/tests/desktop-local-service.test.mjs`
   - `npm run verify:desktop-rust-unit`
7. 복원 파일을 제외한 제품·테스트 코드가 변경되지 않았는지 확인한다.

## 5. 제외 범위·안전 경계

- 승인한 두 경로 이외의 `git restore`, `checkout`, `reset`, `clean`
- 나머지 삭제 31건과 미추적 문서 3건 수정·Stage·Commit
- 복원 파일 내용 편집 또는 새로운 구현 추가
- Windows Recovery Adapter 구현, Build, 설치, 실행
- Branch·Worktree·Clone 생성
- 서버·DB·Docker 변경

## 6. 완료 조건

1. 승인 두 파일만 HEAD Blob과 동일하게 복원된다.
2. 두 `git hash-object`가 예상 Blob과 정확히 일치한다.
3. 나머지 삭제 31건과 미추적 사용자 문서 3건이 보존된다.
4. 관련 Node·Rust 테스트 결과가 기록된다. 환경·선행 결함이면 원인과 범위를 사실대로 분리한다.
5. `git diff --check`와 허용 범위 검사가 통과한다.
6. 진행 기록과 완료보고가 존재한다.

## 7. 허용 변경 경로

- `apps/desktop/src-tauri/src/bin/local-service-lifecycle-host.rs`의 HEAD 원본 복원
- `apps/desktop/src-tauri/tests/fixtures/local-service-error-fixture.mjs`의 HEAD 원본 복원
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-RESTORE-01_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-RESTORE-01_completion_report.md`
- `docs/03_evidence/release_1/R1-M5-07-WINDOWS-RESTORE-01/**`

## 8. 진행 기록

착수, HEAD Blob 확인, 복원, Hash 대조, 각 테스트, Dirty 보존 확인과 종료 직전에 다음 필드를 기록한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

## 9. 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

`COMPLETED`는 승인된 두 파일 복원과 무결성 확인 완료만 뜻한다. Windows Recovery 또는 M5 Exit PASS를 주장하지 않는다.

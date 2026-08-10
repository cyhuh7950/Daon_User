# R1-M5-07 Windows 설치형 검증 복원 범위 조사 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WINDOWS-SCOPE-01` |
| issue_id | `R1-M5-07-WINDOWS-SCOPE-01-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 승인 | 신산님 2026-08-10 M5 후속 검증 진행 승인 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 기준 Commit | `4028390` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-SCOPE-01_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-SCOPE-01_completion_report.md` |

## 2. 승인 기준

다음 문서를 EOF까지 읽고 Hash·승인·적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.9
- `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.7 §15·§21~24
- `docs/04_test_reports/release_1_test_plan.md` 0.9
- `docs/02_work_orders/release_1/R1-M5-07_work_order.md`
- `docs/04_test_reports/release_1/R1-M5-07_completion_report.md`
- `docs/03_evidence/release_1/R1-M5-07/manifest.json`
- `docs/04_test_reports/release_1/M5_milestone_exit_retrospective_2026-08-10.md`
- `docs/04_test_reports/release_1/verification_debt_2026-08-04.md`

## 3. 목표

현재 공식 Working Tree의 사용자 삭제 33건을 복원하지 않은 채, M5 Windows 설치형 Backup/Restore·Local 복구 검증에 실제로 필요한 파일과 선행 조건을 증거로 식별한다. 전체 삭제를 일괄 복원하지 않고 최소 복원 후보, 복원 불필요 항목, Build·설치·화면/API 검증 절차와 위험을 분리해 신산님 결정 자료를 작성한다.

이 작업은 조사만 수행한다. 파일 복원, Build, 설치, 실행, Commit된 제품 코드 변경을 하지 않는다.

## 4. 포함 범위

- `git status`, `git diff --name-status`, `git ls-tree`, `git show HEAD:<path>`를 이용한 삭제 33건의 HEAD 존재·내용 확인
- Desktop/Tauri package·Cargo·Build script·test·fixture 의존성의 읽기 전용 추적
- M5-07 작업지시·완료·Evidence의 Windows 완료 조건 대조
- 삭제 파일을 다음으로 분류
  - Windows M5 증거에 직접 필요
  - Build/Runtime에 간접 필요
  - Mobile/Web 전용으로 이번 Windows 증거와 무관
  - 추가 확인 필요
- 최소 복원 후보별 경로·필요 이유·기존 사용자 변경 충돌 위험·복원 후 검증 명령 정리
- Windows 설치형 검증의 예상 단계와 성공·실패·정리 증거 정의
- 사용자가 승인해야 할 정확한 복원 범위 제안

## 5. 제외 범위·안전 경계

- `git restore`, `checkout`, `reset`, `clean`, 파일 복사·생성으로 삭제 파일 복원
- Branch·Worktree·Clone·임시 소스 폴더 생성
- Build·설치·실행·서명·Keystore 변경
- 제품·테스트 코드, 설정, 의존성, 설계·계획 변경
- 사용자 삭제·미추적 파일 Stage·Commit
- ysna-server·WSL-server·DB·Docker 변경

삭제 상태의 이유를 추측으로 확정하지 않는다. HEAD 내용과 의존성 증거만으로 복원 필요성을 판정하고, 사용자 의도는 별도 승인 대상으로 남긴다.

## 6. 완료 조건

1. 삭제 33건 전체가 분류표에 한 번씩 포함된다.
2. Windows 증거에 직접·간접 필요한 최소 복원 후보가 경로 단위로 제시된다.
3. 각 후보에 HEAD 존재, 참조 지점, 필요한 Build/Test/화면 단계와 회귀 위험 근거가 연결된다.
4. 복원 없이 가능한 검증과 복원 후에만 가능한 검증이 분리된다.
5. 실제 복원·Build·설치·서버 변경 0건과 기존 Dirty 보존이 확인된다.
6. 보고서의 경로·명령·판정이 `git diff --check`와 허용 범위 검사에 통과한다.

## 7. 허용 변경 경로

- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-SCOPE-01_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-SCOPE-01_completion_report.md`
- `docs/03_evidence/release_1/R1-M5-07-WINDOWS-SCOPE-01/**`

## 8. 보존 대상

- 기존 사용자 삭제 33건
- 기존 미추적 사용자 문서 3건
- 제품·테스트·설정 파일 전체
- 운영·개발 서버와 DB·Object 데이터

## 9. 진행 기록

착수, 정본 확인, 문서 검토, 삭제 목록 확정, 의존성 조사, 분류 완료, 최소 후보 도출, 검증 완료와 종료 직전에 다음 필드로 기록한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

## 10. 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

`COMPLETED`는 조사와 결정 자료 완료만 뜻한다. Windows 제품 검증 PASS나 M5 Exit를 주장하지 않는다.

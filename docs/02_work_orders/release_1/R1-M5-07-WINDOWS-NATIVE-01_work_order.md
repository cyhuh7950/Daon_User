# R1-M5-07 Windows Native 설치형 통합 검증 작업지시서

## 1. 승인 기준과 Writer

- Work Order ID: `R1-M5-07-WINDOWS-NATIVE-01`; Issue ID: `R1-M5-07-WINDOWS-NATIVE-01-I001`.
- 상태: `READY` · 2026-08-11.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; 승인 기준 HEAD: `66742bfd64c9799686875fd7ecae237b2cb3bd0c`.
- 신산님이 `master` 단독 작업과 승인 계획의 연속 진행을 지시했으므로 Branch·Worktree를 생성하지 않는다. 어울2가 이 범위의 유일 Writer다.
- 착수 전 `AGENTS.md`, `docs/superpowers/specs/2026-08-10-windows-recovery-adapter-design.md` 버전 1.3, `docs/superpowers/plans/2026-08-10-windows-recovery-native-bridge.md`의 Global Constraints·Task 6·Completion Contract, `docs/02_work_orders/approvals/APR-R1-M5-07-WINDOWS-NATIVE-LOGIN-20260810-01.md`, C10B-R01 및 C10-R02 Completion Report를 EOF까지 읽고 SHA-256과 적용 조항을 진행 기록에 남긴다.
- 실제 Windows 화면 조작에는 `computer-use:computer-use` Skill을 먼저 EOF까지 읽고 적용한다. 새 검증 창만 사용하고 종료 즉시 사용자 제어를 반환한다.

## 2. 단일 목표와 판정 경계

- 목표: Commit `66742bfd64c9799686875fd7ecae237b2cb3bd0c`의 Windows Native 로그인, Recovery 권한 Projection, Cloud 7·Local 3 Command, React Adapter가 NSIS 설치형 실제 화면에서 연결되는지 자동 회귀와 실제 증거로 검증한다.
- 이 작업은 검증 전용이다. 제품 코드·테스트 계약·API·OpenAPI·Rust·React·의존성·설정을 수정하지 않는다. 제품 결함을 발견하면 우회하지 말고 재현 증거와 영향 범위를 `FAILURE_REPORT`로 남겨 별도 보정 Work Order 판단을 요청한다.
- 자동 테스트 PASS와 설치형 실제 PASS를 분리한다. 화면·실제 호출·Trace/Audit 상관관계를 확보하지 못한 항목은 `NOT_PROVEN` 또는 `BLOCKED`이며 M5 Exit·R1-WIN-01 PASS로 승격하지 않는다.
- 운영 Restore Execute·Cancel, 운영 Backup 생성, 파괴적 손상 주입, Credential 삭제·초기화, DB·사용자 문서 변경은 금지한다.

## 3. 허용 산출물

- Create/append: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-01_progress.md`
- Create: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-01_completion_report.md`
- Create: `docs/03_evidence/release_1/R1-M5-07-WINDOWS-NATIVE-01/manifest.json`
- Create: `docs/03_evidence/release_1/R1-M5-07-WINDOWS-NATIVE-01/verification-summary.md`
- Create: 위 Evidence 디렉터리 아래의 명령 결과 요약, PNG 화면 캡처, DOM 또는 접근성 Snapshot, Process·Port·Installer·Trace/Audit 대조 자료. 원본 Credential·Password·Authorization·Refresh·Local Token·Gateway 내부주소·Loopback Port는 저장하지 않는다.

허용 경로 밖 수정·Stage를 금지한다. Build가 만든 ignored `apps/desktop/src-tauri/gen`, Sidecar, 격리 Cargo Target은 소유권과 절대경로를 확인한 뒤 정본 Wrapper의 정상 정리 절차만 사용한다.

## 4. 기존 상태와 사용자 데이터 보호

- 착수 시 Git root·Branch·origin·HEAD·`origin/master`·`git status --short`를 기록한다.
- 사용자 기존 삭제 31건과 미추적 문서 3건을 복원·수정·Stage하지 않는다. `npm run verify:workspace`가 삭제된 Web Route 때문에 실패하면 파일·오류·선행 상태를 분리하고 이번 변경 실패로 오인하지 않는다.
- 설치 전 실행 중 Daon Process, 기존 설치 경로·버전·HKCU Uninstall 등록, Local Storage 경로 존재 여부, Credential Target의 존재 여부만 기록한다. Secret·Blob·Password 값은 읽지 않는다.
- 기존 설치와 사용자 데이터가 있으면 덮어쓰기·삭제·초기화하지 않는다. Installer는 승인된 현재 사용자 설치 경로만 사용하고, Uninstall이나 데이터 삭제는 수행하지 않는다.
- Local Scan·Job·Repair는 기존 사용자 자료가 아닌 명시적으로 식별된 전용 `fixture-*` 대상에서만 수행한다. 전용 Fixture임을 증명할 수 없으면 Repair를 실행하지 않고 `BLOCKED_FIXTURE_UNAVAILABLE`로 기록한다.
- Cloud Preview도 승인된 Fixture ID·Workspace·Step-up 조건이 확인될 때만 수행한다. Execute·Cancel은 누르지 않는다.

## 5. 자동 회귀와 Build

다음 명령을 순서대로 실행하고 종료 코드·수치·시간·환경을 기록한다.

```powershell
uv run --project services/api python -m pytest services/api/tests -q
uv run --project services/local-service python -m pytest services/local-service/tests -q
node scripts/verify-openapi-contract.mjs
npm run verify:desktop-unit
npm run verify:desktop-type
npm run verify:workspace
npm run lint:workspace
npm run build:desktop-installer
git diff --check
```

- 저장소 `.venv`가 손상됐으면 파일을 고치거나 설치하지 말고, 저장소 Lock/Requirements를 사용하는 임시 격리 `uv run --isolated`로 동등 명령을 1회 실행해 원 명령 실패와 대체 근거를 모두 기록한다.
- 장시간 Cargo/NSIS는 단일 Process만 실행하고 중복 재시작하지 않는다. 호출 제한이 끝나도 Child가 생존하면 읽기 전용 Process 확인으로 동일 실행을 추적한다.
- Installer 경로·크기·SHA-256·Authenticode 상태·포함 Sidecar SHA를 기록한다. Unsigned Development Build면 사실대로 표시하고 운영 서명 PASS를 주장하지 않는다.

## 6. 실제 Windows 설치형 검증

1. 설치 전 보호 상태와 실행 Process 0을 확인하고 생성된 NSIS를 현재 사용자 범위에 설치한다.
2. 설치된 Daon 앱을 시작해 1920×1080 기준 화면과 Native 로그인 UI를 확인한다.
3. Password는 신산님이 화면에 직접 입력하는 경우에만 사용한다. 자동화·로그·Clipboard·DOM Snapshot·Evidence에서 값을 읽거나 저장하지 않는다. 입력이 없으면 기존 완료 증거를 보존하고 `BLOCKED_AUTHENTICATION_REQUIRED`로 보고한다.
4. 로그인 성공 후 Safe Session 식별자와 `recovery_operations`의 빈 배열 또는 정확한 7종만 화면 상태로 확인한다. Credential 원문은 확인하지 않는다.
5. 권한이 없으면 Cloud 버튼 비활성·Handler invoke 0을 확인하고 종료한다. 권한이 있으면 Cloud 목록 읽기 1회만 실행한다.
6. 승인된 Fixture와 Step-up이 있으면 Preview 1회만 실행하고 결과 ID·Trace·Audit을 대조한다. Execute·Cancel·Backup Create는 실행하지 않는다.
7. Local Service가 `ready`이고 전용 Fixture가 증명되면 Scan→Job 조회→Repair 1회를 명시 버튼으로 실행한다. 준비되지 않았거나 Fixture가 없으면 Cloud로 자동 전환되지 않음을 확인하고 해당 단계만 BLOCKED로 남긴다.
8. Logout 후 Session/권한/Recovery 결과가 재노출되지 않고 늦은 Poll이 복원하지 않는지 확인한다.
9. 앱을 정상 종료하고 Daon App·Sidecar Process 0, Listener·Port 잔여 0을 확인한다. 다른 프로젝트 Process는 종료하지 않는다.

각 상태 전이는 원본 PNG와 접근성/DOM Snapshot으로 보존하되 Password·Credential·내부 URL·Loopback Port가 보이면 캡처하지 말고 Safe redaction 요약만 남긴다. 실제 호출 URL·Method·Status를 도구가 제공하지 않으면 추측하지 않고 `NOT_OBSERVED`로 기록한다.

## 7. Evidence Manifest 계약

- `manifest.json`은 Build Commit, 문서 Hash, 환경, Installer Hash/서명, 자동 테스트별 결과, 설치·실행·로그인·Cloud·Local·종료 각 단계의 `PASS|FAIL|BLOCKED|NOT_OBSERVED`, 원본 파일 경로와 SHA-256을 기록한다.
- `verification-summary.md`는 `판정 → 판단 이유 → 조치` 순서로 작성한다.
- Trace/Audit은 동일 Trace ID와 시간·Actor·Workspace·Action이 실제 API/Audit 근거에서 상관될 때만 `PROVEN`이다. 화면 Trace만 있으면 `SCREEN_ONLY`, 원본을 얻지 못하면 `NOT_PROVEN`이다.
- Manifest JSON Parse, 기재 파일 SHA 전수 일치, Secret scan 0, `git diff --check`, 허용 경로 Diff를 최종 확인한다.

## 8. 진행·결과 계약

- 진행 기록은 착수, 문서 검토, 각 회귀, Build, 설치 전 상태, 설치, 각 화면 단계, 오류·원인·복구, 종료·정리와 최종 검증 직후 갱신한다.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
- 모든 자동 회귀와 허용 실제 단계가 증거로 충족되면 `COMPLETED`를 사용한다. 자격증명·Fixture·GUI·환경 부재는 `BLOCKED`이며 실패 횟수에 포함하지 않는다. 제품 결함은 재현 가능한 `FAILURE_REPORT`로 제출한다.
- Commit·Push·배포·운영 Restore·사용자 데이터 삭제는 어울1 소유이므로 수행하지 않는다.

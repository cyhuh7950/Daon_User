# R1-M5-07-WINDOWS-SCOPE-01 완료보고

## 판정

`COMPLETED / INVESTIGATION_ONLY`

삭제 33건을 실제 복원하지 않고 전수 분류했으며, Windows 설치형 검증의 최소 복원 후보와 복원 전·후 검증 절차를 확정했다. 이 판정은 조사 산출물 완료만 뜻하며 Windows 제품 검증 PASS 또는 M5 Exit를 뜻하지 않는다.

## 판단 이유

1. 공식 작업공간 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, Branch `master`, HEAD·origin/master `c76d881627e2a740557f22aa9d81700cfdf36267`을 확인했다.
2. 기존 삭제 33건을 HEAD tree와 비교해 직접 필요 1, 간접 필요 1, 이번 Windows 증거와 무관 31, 추가 확인 0으로 중복 없이 분류했다.
3. 최소 복원 후보는 `local-service-lifecycle-host.rs`와 `local-service-error-fixture.mjs` 두 경로다. 전자는 Windows Local Service Manager lifecycle 실제 실행에 직접 필요하고, 후자는 Windows 오류·Retry·Process tree 정리 회귀에 간접 필요하다.
4. Mobile 23건과 Web 8건은 Desktop package/Cargo/Tauri 입력이 아니다. 특히 삭제된 Web BFF route를 복원해도 Desktop는 이를 import하지 않으며 Tauri CSP가 `connect-src 'none'`이라 Windows Recovery 연결을 만들지 않는다.
5. 현재 Desktop shell은 공용 Operations UI를 표시하지만 `recoveryAdapter`를 전달하지 않고, Tauri command도 Local Service 상태·재시도 2개뿐이다. 따라서 두 후보 복원만으로 actual Windows Cloud Backup/Restore와 Local Recovery 3개 API 화면 여정을 검증할 수 없다.
6. 복원·Build·설치·실행·제품/테스트 코드 변경·Stage·Commit·Push·Deploy·서버/DB/Docker 변경은 수행하지 않았다.

## 조치

1. 신산님은 최소 후보 2개를 HEAD blob 그대로 복원할지 경로 단위로 결정한다.
2. 승인 시 별도 복원·검증 작업지시서에서 나머지 삭제 31건과 미추적 사용자 문서 3건을 보존한 채 Hash 확인 → Desktop 정적/Rust 회귀 → NSIS Build → 설치·Process/Port lifecycle을 순서대로 수행한다.
3. R1-M5-07 Windows 완료 조건을 달성하려면 복원과 분리하여 Windows용 Production-bound `BackupRestoreAdapter` 및 Local Recovery scan/status/repair Adapter 연결 작업지시서를 작성해야 한다.
4. G9-DRILL 승인 전 운영 Restore·제자리 덮어쓰기·파괴적 손상 주입은 계속 금지한다.

## 생성 결과

- `docs/03_evidence/release_1/R1-M5-07-WINDOWS-SCOPE-01/scope-analysis.md`
- `docs/03_evidence/release_1/R1-M5-07-WINDOWS-SCOPE-01/manifest.json`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-SCOPE-01_progress.md`
- 이 완료보고

## 미해결 사항

- 사용자 삭제 2개 최소 후보의 실제 복원 승인 여부
- Windows Cloud Backup/Restore Adapter와 Local Recovery Adapter 연결 구현 승인·작업지시
- 실제 Windows NSIS 설치형 화면/API·Process·Port·Audit/Trace 증거

## 결과 계약

`COMPLETED | R1-M5-07-WINDOWS-SCOPE-01-I001 | 삭제 33건 HEAD·참조·의존성 전수 조사 및 분류 | 최소 복원 후보 2건, 복원 전·후 검증 절차, 별도 구현 공백 보고 | 정적 경로·HEAD blob·참조 대조 완료; 복원·Build·실행 0건 | Windows Adapter 미연결과 actual 설치형 증거 미확보 | 신산님의 후보 2개 복원 여부 및 별도 Windows Adapter 작업지시 판단 필요`

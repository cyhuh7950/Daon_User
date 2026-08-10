# R1-M5-07 Windows Native Session Vault·HTTPS Client 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WINDOWS-NATIVE-SESSION-C04` |
| issue_id | `R1-M5-07-WINDOWS-NATIVE-SESSION-C04-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 선행 기준 | `81b04887f3aec2c5800d4f6210076e0bca4b9b08` · Task 1 검토 승인 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C04_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C04_completion_report.md` |

## 2. 승인 기준

다음을 EOF까지 읽고 Hash·승인 상태·적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- `docs/superpowers/specs/2026-07-20-daon-user-program-design.md`
- `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.8
- `docs/superpowers/specs/2026-08-10-windows-recovery-adapter-design.md` 1.1
- `docs/superpowers/plans/2026-08-10-windows-recovery-native-bridge.md` Task 2
- `docs/02_work_orders/approvals/APR-R1-M5-07-WINDOWS-NATIVE-LOGIN-20260810-01.md`
- `docs/04_test_reports/release_1_test_plan.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-AUTH-C03_completion_report.md`

## 3. 목표

Task 1의 Native Credential 응답을 Rust가 직접 수신·보관·회전할 수 있도록 전용 Windows Credential Manager Vault와 HTTPS Identity Client를 구현한다. Credential은 JavaScript·Log·환경 변수·Evidence에 노출하지 않는다.

## 4. 구현 계약

1. `DaonUser/NativeSession/v1` Credential Target을 사용하고 기존 `DaonUser/LocalStorage/v1` Root Key Target·형식·수명주기와 분리한다.
2. Native Session Secret은 Access·Refresh·Safe Session Projection을 포함하되 Debug는 Redact하고 가능한 메모리 버퍼는 Drop에서 지운다.
3. `reqwest = { version = "=0.13.4", default-features = false, features = ["json", "rustls"] }`를 정확히 Pin하고 Cargo.lock을 갱신한다.
4. Public Gateway는 패키지에 컴파일된 `https://daon-user.sinsan.kr`만 허용한다. `.env`, `NEXT_PUBLIC_*`, HTTP, localhost, 127.0.0.1, Docker Host·Port를 사용하지 않는다.
5. Identity Client는 `POST /api/v1/auth/native/login`, `POST /api/v1/session/refresh`만 호출한다. Password·Credential·Authorization Header·원 응답을 Debug·Safe Error에 포함하지 않는다.
6. Tauri Command는 `native_login`, `native_logout`, `native_session_status` 세 개만 추가한다. JavaScript에는 로그인 상태와 user/tenant/workspace/session/device 식별자, Safe Error만 반환한다.
7. Access 만료 시 Refresh 회전은 최대 1회다. Refresh 실패·재사용·철회는 Vault를 폐기하고 `AUTHENTICATION_REQUIRED`로 닫는다.
8. 상태 변경 업무 요청의 자동 재실행은 하지 않는다. 본 작업에서는 Recovery Cloud 호출을 구현하지 않는다.
9. 기존 Local Storage Credential Store·Local Service Lifecycle·CSP·Web 코드를 변경하지 않는다.
10. Test Transport 또는 순수 Domain Port로 Login·Refresh·오류를 검증하며 실제 사용자 Password나 운영 Credential을 사용하지 않는다.

## 5. TDD 필수 사례

- Target 분리와 기존 Local Root Key 회귀
- Vault write/read/revoke, invalid/corrupt Blob fail-close
- Debug·Safe DTO·Error 내 Credential·Password·Gateway 원문 0건
- HTTPS·고정 Gateway·허용 Path만 통과, HTTP/localhost/다른 Path 거부
- Login 성공 Projection과 Cookie 비의존
- Refresh 1회 회전·Vault 교체, 재사용/실패 시 revoke·`AUTHENTICATION_REQUIRED`
- Tauri Command 응답에 Credential Field 0건

## 6. 필수 검증

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
node --test scripts/tests/desktop-tauri-shell.test.mjs
node --check apps/desktop/src/desktop-shell.jsx
npm run verify:desktop-lint
git diff --check
```

Dependency Fetch·Compile은 장시간이 걸릴 수 있으므로 충분히 기다리고, 동일 명령을 중복 실행하지 않는다. 자동 테스트는 실제 설치형 로그인 PASS가 아니다.

## 7. 허용 변경 경로

- `apps/desktop/src-tauri/Cargo.toml`
- `apps/desktop/src-tauri/Cargo.lock`
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/src/native_session.rs`
- `apps/desktop/src-tauri/tests/native_session_contract.rs`
- `scripts/tests/desktop-tauri-shell.test.mjs`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C04_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C04_completion_report.md`

허용 경로 밖 변경이 필요하면 코드를 수정하지 말고 증거와 함께 어울1에게 되돌린다.

## 8. 보존 대상

- 사용자 삭제 31건과 미추적 문서 3건
- 기존 Web·Native Identity API와 Task 1 증거
- 복원한 Windows lifecycle host·error fixture HEAD Blob
- Local Service Credential·Process·Storage와 기존 Desktop Shell
- 실제 사용자·운영 Password·Token·DB·Backup 데이터

## 9. 진행·결과 계약

착수, 정본 확인, RED, Dependency 갱신, 최소 구현, 각 테스트, 오류·복구, 종료 직전에 다음 형식으로 기록한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

Commit·Push·배포·Browser·실제 로그인·설치는 수행하지 않는다.

결과는 다음으로 보고한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

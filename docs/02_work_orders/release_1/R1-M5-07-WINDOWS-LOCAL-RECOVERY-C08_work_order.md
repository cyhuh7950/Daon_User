# R1-M5-07 Windows Local Recovery Native Port 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08` |
| issue_id | `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 선행 기준 | Native Session C07 `COMPLETED` · Commit `81cfe0e` · 문서 마감 `79860b8` |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08_completion_report.md` |

## 2. 승인 기준

다음을 EOF까지 읽고 Hash·승인 상태·적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- `docs/superpowers/specs/2026-07-20-daon-user-program-design.md`
- `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.8
- `docs/superpowers/specs/2026-08-10-windows-recovery-adapter-design.md` 1.1
- `docs/superpowers/plans/2026-08-10-windows-recovery-native-bridge.md` Task 3
- `docs/02_work_orders/approvals/APR-R1-M5-07-WINDOWS-NATIVE-LOGIN-20260810-01.md`
- `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` §7.2 `R1-D027` 확정 결정
- `docs/04_test_reports/release_1_test_plan.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C07_completion_report.md`

## 3. 목표

Windows Rust가 준비된 `LocalServiceManager`의 동적 Loopback Context를 내부에서만 사용해 Local Recovery Scan·Job 조회·Repair 3종을 호출하는 `LocalRecoveryPort`를 구현한다. Port·Token·Root Secret·격리 경로는 JavaScript와 공개 DTO에 노출하지 않는다.

## 4. 구현 계약

1. 기존 Runtime·Storage Scope/Capability Allowlist를 보존하고 다음 세 쌍만 추가한다.
   - Scan: `recovery.write` · `recovery.scan`
   - Job 조회: `recovery.read` · `recovery.job.read`
   - Repair: `recovery.write` · `recovery.repair`
2. 정확히 다음 경로만 허용한다.
   - `POST /local/v1/recovery/scans`
   - `GET /local/v1/recovery/jobs/{id}`
   - `POST /local/v1/recovery/jobs/{id}/repair`
3. `LocalServiceManager`는 준비된 Instance의 동적 Loopback 주소와 App Instance Credential을 Rust 내부 Port에만 제공한다. 공개 상태·Tauri DTO·Debug·Log에는 Port·Token·Root Secret을 포함하지 않는다.
4. 요청마다 Scope/Capability에 맞는 단기 Command Token을 새로 발급한다. 다른 조합·경로·HTTP Method는 fail-close한다.
5. Job ID·요청 Body·응답 Content-Type·Content-Length·실제 수집 크기·Connect/Request Timeout을 제한한다.
6. Local Service 미준비는 `LOCAL_SERVICE_UNAVAILABLE`, 입력·Capability·응답 위조는 승인된 Safe Error로 닫는다. Cloud Recovery로 자동 전환하지 않는다.
7. 응답은 승인된 Local Recovery DTO만 반환하고 Header·Credential·내부 URL·Port·Storage Root·격리 경로를 제거한다.
8. 기존 Local Service Lifecycle·Storage 암호화·Runtime Read 계약·Native Session·Web·CSP 동작을 변경하지 않는다.
9. 실제 운영 데이터 Restore, 제자리 덮어쓰기, 파괴적 손상 주입은 수행하지 않는다.

## 5. TDD 필수 사례

- 기존 Runtime/Storage Allowlist 전부 유지 + Recovery 세 쌍만 추가 + 교차 조합 거부
- Local Service 미준비 시 Network 호출 0건과 `LOCAL_SERVICE_UNAVAILABLE`
- 세 Method·Path만 허용하고 잘못된 Job ID·경로·Method 거부
- 요청별 Token 발급과 Scope/Capability 일치, 재사용·다른 Command 사용 거부
- Port·Token·Root Secret·격리 경로가 DTO·Debug·Log·Error에 0건
- Timeout, 비 JSON, oversize·truncated·unknown field 응답 fail-close
- Cloud Fallback 0건
- 기존 Local Service Rust·Node·Python Recovery 회귀

## 6. 필수 검증

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
uv run --project services/local-service python -m pytest services/local-service/tests/test_recovery.py -q
node --test scripts/tests/desktop-local-service.test.mjs
npm run verify:desktop-lint
git diff --check
```

장시간 Compile은 충분히 기다리고 동일 명령을 중복 실행하지 않는다. 자동 테스트는 실제 Windows 설치형 Local Recovery PASS가 아니다.

## 7. 허용 변경 경로

- `apps/desktop/src-tauri/src/local_service.rs`
- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/tests/local_service_contract.rs`
- `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- `scripts/run-isolated-desktop-cargo.mjs`
- `scripts/tests/desktop-local-service.test.mjs`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08_completion_report.md`

허용 경로 밖 변경이 필요하면 코드를 수정하지 말고 증거와 함께 어울1에게 되돌린다.

## 8. 보존 대상

- 사용자 삭제 31건과 사용자 미추적 문서 3건
- Native Session C07 제품·테스트·증거와 Credential Manager Target
- 복원된 lifecycle host·error fixture HEAD Blob
- Local Storage Root Key·Storage 형식·기존 Runtime Read Allowlist
- 기존 Web·Cloud Recovery API·CSP·same-origin 경계
- 실제 사용자 Credential·운영 데이터·DB·Backup

## 9. 진행·결과 계약

착수, 정본 확인, RED, 최소 구현, 각 테스트, 오류·복구, 종료 직전에 다음 형식으로 기록한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

Commit·Push·배포·Browser·실제 설치·운영 데이터 Restore는 수행하지 않는다.

결과는 다음으로 보고한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

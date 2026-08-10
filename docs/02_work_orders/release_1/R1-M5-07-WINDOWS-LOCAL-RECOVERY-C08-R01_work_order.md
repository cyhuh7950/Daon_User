# R1-M5-07 Windows Local Recovery Native Port 1차 재작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-R01` |
| 원 Work Order | `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08` |
| issue_id | `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 판정 | 원 결과 `INCOMPLETE` 1회 · 유효 `FAILURE_REPORT` 0회 |
| 기준선 | `master` · HEAD/origin `bc3860e` + C08 Working Tree |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-R01_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-R01_completion_report.md` |

## 2. 재작업 근거

C08 자동 회귀는 통과했으나 내부 독립 읽기 전용 검토에서 Critical 0건·Important 5건·Minor 1건이 확인되어 기술 수락하지 않는다. 승인된 기능 범위는 변경하지 않고 다음 보안·정본·운영 결함만 최소 보정한다.

## 3. 필수 재작업 계약

### R01-1 Native Context 비노출

- `AppCredentials::Debug`의 `storage_root`를 실제 값 대신 `[redacted]`로 고정한다.
- 동적 Port·Root Secret·Token·Storage Root·격리 경로 Canary를 Local Recovery 응답의 문자열 필드에 주입했을 때 Safe DTO로 통과하지 못하도록 제품 경계에서 검증한다.
- `recorded_at`은 Local Storage 정본과 같은 UTC Timestamp 형식만 허용한다.

### R01-2 LocalRecoveryJob 정본 Schema 일치

- 상태 Allowlist에 `failed`를 포함한다.
- `recorded_at`은 실제 UTC RFC3339 정본 형식을 검증한다.
- `version == 1`이면 `previous_version == None`, `version > 1`이면 `previous_version == version - 1`을 강제한다.
- 합법적인 `failed` Job PASS와 malformed Timestamp·Version 연결 FAIL을 TDD로 고정한다.

### R01-3 실제 Parser 오류의 Safe Error 보존

- `LOCAL_RECOVERY_RESPONSE_REJECTED`를 `code` 그대로, `retryable=false`로 보존한다.
- malformed·oversize·truncated·위조 Status가 실제 TCP Parser를 거쳐 `LocalRecoveryPort` 최종 Safe Error까지 전달되는 경로를 검증한다.
- 연결 실패·실제 일시 장애와 보안 위조 응답을 같은 retryable 오류로 합치지 않는다.

### R01-4 전체 Deadline과 Lifecycle 비차단

- Connect/Read/Write 개별 Timeout 외에 요청 전체 elapsed/deadline 상한을 적용한다.
- Runtime Mutex 안에서는 ready·running 확인과 Port·요청별 Token Snapshot까지만 수행하고, 실제 TCP I/O 전에 Lock을 해제한다.
- Slow-drip Loopback 서버에서도 전체 Deadline 안에 종료하고, 동시에 `status`와 `shutdown`/Lifecycle 접근이 Network I/O에 막히지 않음을 결정론적으로 검증한다.
- 동기 네트워크 호출을 Tauri Event Thread에서 직접 수행하는 공개 Command는 이번 범위에 추가하지 않는다.

### R01-5 실제 제품 경로 검증 강화

- FakeTransport만으로 완료를 주장하지 않는다. `LocalServiceManager → 실제 Loopback TCP 작성/수신 → Parser → LocalRecoveryPort Safe Error` 제품 경로 Harness를 추가한다.
- 정확한 Method·Path·Authorization Command Binding, Token replay/다른 Command 거부, Header 상한, Content-Length/실제 크기, oversize·truncated·slow timeout·Secret Canary를 검증한다.
- Local Service Python은 `services/local-service/tests/test_recovery.py`뿐 아니라 인증된 HTTP Recovery 계약이 있는 `services/local-service/tests/test_app.py`도 실행한다.
- Completion Report에 각 요구사항과 이를 입증하는 테스트 이름을 매핑한다.

### R01-6 Safe Error Projection

- 설계 정본 `{ code, trace_id, retryable }`에 맞춰 `LocalRecoveryError`에 Secret·내부주소가 없는 불투명 `trace_id`를 추가한다.
- 오류마다 비어 있지 않은 고정 형식 Trace를 만들고 직렬화·Debug에 Credential·Port·Root·경로가 포함되지 않음을 검증한다.

## 4. TDD·검증

각 R01 항목은 보정 전 실제 RED를 먼저 확인하고 단계별 Progress에 기록한다. 최종적으로 다음을 새로 실행한다.

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
uv run --isolated --project services/local-service --frozen python -m pytest services/local-service/tests/test_recovery.py services/local-service/tests/test_app.py -q
node --test scripts/tests/desktop-local-service.test.mjs
npm run verify:desktop-lint
git diff --check
```

Rustfmt는 본 작업 소유 Rust 파일에만 적용한다. 전체 저장소 포맷으로 무관 파일을 변경하지 않는다.

## 5. 허용 변경 경로

- 원 C08 허용 제품·테스트 파일 6개
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08_completion_report.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-R01_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-R01_completion_report.md`

`services/local-service` 제품·테스트는 읽기 및 실행만 허용한다. 실제 결함이 확인되어 수정이 필요하면 변경하지 말고 증거와 함께 어울1에게 되돌린다.

## 6. 보존·금지

- 사용자 삭제 31건과 원래 미추적 문서 3건을 보존한다.
- C07 Native Session, 기존 Lifecycle·Storage·Web·CSP·Cloud Recovery를 변경하지 않는다.
- 실제 운영 Credential·데이터·Restore·파괴적 손상 주입을 사용하지 않는다.
- Commit·Push·배포·Browser·실제 설치를 수행하지 않는다.
- 장시간 Cargo는 한 번만 실행하고 중복 실행하지 않는다.

## 7. 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

원 C08 완료보고는 과거 실행 기록으로 보존한다. R01 결과는 별도 Progress·Completion Report에 기록하며, 원 보고의 과장 또는 정정이 필요하면 append-only 정정으로 남긴다.

# R1-M3-06-C24 수정 작업지시서 — Permission 서비스 실패 증거 보강

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `25` |
| 사유 | C23 exact-SHA에서 System Open UI Test는 성공했으나 Simulator Shell의 첫 Permission Phase가 `NSPOSIXErrorDomain code=1 / Failed to set access`로 종료됐고, 함수 내부 ERR가 상위 단계 표식을 출력하지 않아 실패 서비스가 확정되지 않음 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-25.md` |

## 2. 확인된 증거

- exact Head `c07fbf50f7cf660f29cdfd6587a0161e02b6429c`의 Quality Gate Run `30252727785`는 SUCCESS다.
- iOS Run `30252726337`은 unsigned Build와 System Open Deep Link를 포함한 UI Test Step까지 SUCCESS다.
- 후속 Simulator Shell은 Home Launch·Ready 뒤 첫 Permission Phase에서 `Simulator device failed to complete the requested operation`, `Failed to set access`, `Operation not permitted`로 종료했다.
- `run_permission_phase` 내부에는 camera → microphone → notifications 순서의 `simctl privacy` 호출이 있으나 안전한 서비스 식별 출력이 없고, 현재 `ERR` Trap은 함수 내부 실패에 상속되지 않아 C23 표식도 출력되지 않았다.
- Apple 공식 Simulator 안내는 `simctl privacy`가 지원되는 보호 자원만 제어한다고 설명한다. 따라서 우회나 서비스 제거 전에 실제 실패 서비스를 exact macOS Runner에서 확정해야 한다.

## 3. 필수 작업

1. `verify-simulator.sh`의 `ERR` Trap이 함수·서브셸 내부 오류에도 상속되도록 Bash errtrace를 활성화하되 `errexit`, `nounset`, `pipefail`, 원 Exit와 EXIT Cleanup 동작을 보존한다.
2. 각 Permission Phase의 camera·microphone·notifications 호출 직전에 현재 서비스를 보유하고, ERR 시 기존 허용 단계명·원 Exit와 함께 `DAON_SIM_FAILED_PERMISSION_SERVICE=<camera|microphone|notifications>`만 출력한다.
3. Permission 밖의 실패에는 서비스 표식을 출력하지 않는다. Phase·서비스 값은 고정 Allowlist 외 값을 출력하지 않는다.
4. 오류를 무시하거나 성공으로 바꾸지 않고, 권한 서비스 제거·순서 변경·Retry 추가·UI Test/Native Host/Workflow/Product 동작 변경을 하지 않는다.
5. 성공 경로의 기존 출력과 Evidence 계약을 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 함수 내부 Exit 23 Fixture에서 단계 표식이 누락되는 RED를 확인한다.
- 구현 후 함수 내부 Permission 실패 Fixture가 허용 단계명·서비스명·원 Exit 23만 출력하고 원 Exit 23으로 종료함을 검증한다.
- Permission 밖 실패와 성공 Fixture에는 서비스 표식이 0건이어야 한다.
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Simulator Script, 관련 계약 Test, Progress와 Attempt 25 보고서뿐이다.
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속이다.


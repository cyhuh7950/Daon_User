# R1-M3-06-C19 수정 작업지시서 — Simulator 검증 초기 Process 경계 고정

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `20` |
| 사유 | C18 Route Key 초기화가 UI Test가 백그라운드에 남긴 기존 Daon Process 실행 중 수행되어 UserDefaults Cache와 JS 초기화 Effect가 재시작되지 않음 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-20.md` |

## 2. 확인된 증거

- exact Head `c736fb163f34f1116c019d7a808a610ce75fba82`, Run `30244183306`에서 Gate·Build·UI Test 3개는 SUCCESS다.
- UI Test 마지막 Settings 경계는 Daon을 Background에 남길 수 있다. 후속 Script는 App Install 뒤 초기 `terminate` 없이 Container Plist를 수정하고 `simctl launch`했다.
- 기존 Process가 재활성화되면 App의 Restore·Listener 초기화 Effect가 다시 실행되지 않으며, 실행 중 UserDefaults Cache와 직접 수정한 Plist의 일관성도 보장되지 않는다.
- 실제 `wait_for_route Home`은 20회 동안 값이 비어 만료됐다. 이는 C18 준비 신호가 새 Process에서 실행되지 않았음을 뒷받침한다.

## 3. 필수 작업

1. `verify-simulator.sh`에서 App 설치 후 Route Key 제거 전에 Daon Bundle Process를 `simctl terminate`한다. 실행 중이 아니어도 초기화가 계속되도록 해당 1회만 명시적으로 허용 처리한다.
2. 순서를 `install → terminate existing process → clear native_route_key → launch new process → wait Home`으로 고정한다.
3. 종료 대상은 exact Simulator와 `com.sinsan.daon`만 허용한다. Simulator 전체 Shutdown·Erase·Uninstall·다른 Process 종료는 금지한다.
4. C18 App Null Restore·Home 저장, Warm Deep Link 7종과 이후 Rejected/Permission/Lifecycle/Crash/종료 검증은 변경하지 않는다.
5. 고정 Sleep·Wait 증가·새 API·Product Source 변경은 금지한다.

## 4. TDD와 완료 조건

- 구현 전 초기 `terminate` 누락과 순서 계약 RED
- 구현 후 exact 순서, 대상 한정, 기존 최종 Cleanup Terminate와 구분, 다른 검증 불변 계약 PASS
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- Product/Native/Bridge/Project/UI Test/Evidence/Signing 변경 0
- 개인 절대경로·Generated Build/Pods/Gem/Test Temp·Signing Asset 잔존 0
- Progress·Attempt 20에 Run 실패 원문, Background Process/UserDefaults 경계와 macOS 재검증 필요를 기록
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속


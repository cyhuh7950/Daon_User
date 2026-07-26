# R1-M3-03-C02 수정 작업지시서 — Shutdown Race·Bounded Job 종료·실제 오류 경로 증거

## 1. 계약

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M3-03` |
| Correction | `R1-M3-03-C02` |
| issue_id | `R1-M3-03-I001` 유지 |
| Attempt | 3 |
| 판정 원인 | C01 읽기 전용 재검토에서 Important 3건 잔존 |
| 개발자 | 동일 어울2 · `daon-developer` |
| Branch/Worktree | `codex/r1-m3-03` · `C:\tmp\Daon_User-r1-m3-03` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-03-C02_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-03_attempt-3.md` |
| 누적 | `FAILURE_REPORT 0`, `INCOMPLETE 2` |

원 R1-M3-03 및 C01의 승인 계약은 계속 적용한다. 테스트·보안·독립성·Job Object·Browser 비노출 경계를 완화하지 않는다.

## 2. 반드시 수정할 Important 3건

### 2.1 Shutdown 중 새 Process Spawn Race

현재 Retry Background 작업은 이전 Service를 Lock 밖에서 정리한 뒤 `run_start()`를 호출한다. 그 사이 `shutdown()`이 실행돼도 `run_start()`가 실제 Spawn 후에야 stale generation을 발견할 수 있다.

완료 계약:

- Shutdown 시작 시 모든 진행 중/예약 Start·Retry를 취소한다.
- 이전 Service `stop()` 대기 중 Shutdown이 들어오면 이후 `launcher.launch()` 호출 횟수가 증가하지 않는다.
- Generation 확인과 Process Spawn 사이 Race를 닫는다. 단순 사전 `if` 확인만으로 완료 처리하지 않는다.
- Manager가 Lock을 잡은 채 장시간 Process I/O를 기다려 UI Status/Shutdown을 막지 않는다.
- 예약 Token/Generation 또는 직렬화된 Spawn Gate를 사용해 Shutdown과 실제 Launch의 원자적 경계를 만든다.
- 종료 이후 Retry·Poll·Background Thread가 새 Child를 만들지 않는다.

필수 Test:

- `retry → previous.stop blocking → shutdown → stop release` 순서에서 Launch Count 불변
- `start reserved → shutdown immediately before spawn`에서 Process 0
- Shutdown과 Retry를 반복 경쟁시켜 Child/Listener 0

### 2.2 Job 종료 실패 뒤 무기한 Wait 제거

현재 3초 뒤 `TerminateJobObject` 반환을 무시하고 무제한 `child.wait()`를 호출할 수 있다.

완료 계약:

- `TerminateJobObject` 성공/실패를 검사한다.
- Job 종료 뒤에도 Bounded Wait를 적용한다.
- Job 종료 실패 또는 제한시간 초과 시 직접 Child Handle 종료를 시도하고 다시 Bounded Wait한다.
- 어떤 경로에서도 App/UI 종료 Thread가 무제한 `wait()`하지 않는다.
- Handle Close·상태 오류는 안정 코드와 내부 제한 증거로 남기고 Secret/Raw 경로를 UI에 노출하지 않는다.
- 실제 Windows API 실패를 주입할 수 있는 경계와 Clock/Wait Test를 둔다.

필수 Test:

- `TerminateJobObject=false`에서 제한시간 안에 반환
- Job 성공이나 Child가 남는 경우 두 번째 제한시간 뒤 반환
- 직접 Child Kill 실패/Wait 실패도 Hang 없이 안정 오류
- 정상 EOF 경로 회귀

### 2.3 실제 Manager 오류 경로와 Tree Cleanup 증거

Fake Launcher가 오류 문자열만 반환하거나 Job을 직접 종료하는 Test로 Manager 정리를 입증하지 않는다.

실제 Fixture Sidecar와 실제 Manager/Job 경로로 다음을 검증한다.

- Ready를 보내지 않는 Startup Timeout
- 잘못된 Ready Envelope
- Ready 후 Health 실패
- EOF를 무시하고 자손 Listener를 유지하는 Shutdown Timeout
- Shutdown과 진행 중 Start/Retry 경쟁

각 Case에서:

- 제한시간 안에 Manager가 반환한다.
- 안정 상태/오류코드가 기대값과 일치한다.
- Parent·자손 Process와 Listener가 0이다.
- 다음 Retry가 허용되는 Case는 새 Credentials로 복구한다.

Headless Host 또는 Windows 전용 Integration Harness가 실제 `LocalServiceManager`의 Production Stop 경로를 호출해야 한다. Test가 `job.terminate()`를 직접 호출해 Manager 경로를 우회하면 증거로 인정하지 않는다.

## 3. 문서·증거 보정

- Attempt 2 보고서의 Source 수를 최종 정본 `43`과 실제 Entries/Patch Hash로 정정한다.
- C01·C02 Progress를 Evidence Manifest에 각각 Hash·Byte로 결속한다.
- Rust Host의 `secrets_emitted:false` 상수 주장은 제거한다. 실제 출력 검사가 없으면 `public_host_evidence_contains_secret_fields:false`처럼 검증 범위를 정확히 표현한다.
- Attempt 1 상단에 “어울1이 INCOMPLETE로 재분류했고 Attempt 2/3이 대체한다”는 이력 배너를 추가한다. 원 개발자 제출 원문과 날짜를 삭제하지 않는다.
- Attempt 2 상단에도 어울1의 `INCOMPLETE` 재분류와 Attempt 3 대체를 명시한다.
- Source Manifest와 Evidence Manifest는 최종 파일 수·Hash·Byte·Dirty Patch Hash를 다시 생성한다.
- 어울2 단계에서는 `dirty_snapshot=true`를 유지한다. Exact Implementation Commit Gate는 어울1 후속이다.

## 4. TDD 순서

1. C02·원 정본·현재 Diff/Evidence Hash와 작업 Process 0을 확인한다.
2. Shutdown/Retry Spawn Race 재현 Test를 먼저 RED로 만든다.
3. 취소/예약/Spawn Gate를 최소 구현해 Race Test GREEN.
4. Job 종료 실패·Bounded Wait Test RED 후 최소 구현 GREEN.
5. 실제 오류 Fixture 5종을 Manager Production 경로로 RED→GREEN.
6. 전체 Rust Manager·Job·Headless Host 회귀.
7. Python 19개, Frontend 10개, Node Runtime·Source Manifest 회귀.
8. Package·실제 Manager Error Cleanup·Process/Listener 0 증거.
9. 전체 7범주 Gate, Manifest, 문서 정합, 생성물 정리.

각 RED·GREEN·오류·복구를 C02 Progress에 즉시 기록한다. 직접 Cargo 실행을 금지하고 보호 Wrapper만 사용한다.

## 5. 완료 조건

- Important 3건의 Code·Test·실제 Windows 증거
- Manager Production Stop 경로를 통한 오류 Fixture 5종 Process/Listener 0
- 모든 Wait가 Bounded임을 Code와 Test로 확인
- Python·Frontend·Runtime·Rust·Job·독립성·보안·7범주 Gate 전부 PASS
- Evidence/Source/Progress/보고서 Hash·Byte 정합
- 절대 사용자 경로·검증하지 않은 Secret 주장 0
- `.coverage`, Cache, staged Sidecar, `gen`, Target, Fixture Process/Listener 0
- `git diff --check` PASS, 승인 정본 8/8, 삭제·관련 없는 변경 0

## 6. 금지·후속

- Test 삭제·완화·Skip/N/A 처리로 통과 금지
- 이름 기반 Process 일괄 종료 금지
- Browser Shell/HTTP/Process Permission과 Loopback/Token/Port 노출 금지
- Commit·Push·PR·Merge·SSH·서버 배포·GUI 금지
- 전체 재작성·무관 Refactor 금지

결과 첫 줄:

```text
COMPLETED | R1-M3-03-I001 | 수정 요약 | 변경·증거 | 테스트 | 남은 위험 | 어울1 검토 요청
```

완료 후 추가 쓰기를 중지한다.

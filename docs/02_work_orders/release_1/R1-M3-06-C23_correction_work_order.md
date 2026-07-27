# R1-M3-06-C23 수정 작업지시서 — Simulator Shell 단계 실패 표식

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `24` |
| 사유 | C22의 새 System Open UI Test 전체는 성공했으나 후속 Simulator Shell이 17초 안에 종료됐고, 현재 GitHub 상세 Log 인증 불가로 실패 명령을 확정할 근거가 없음 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-24.md` |

## 2. 확인된 증거

- exact Head `7973e5def59ca41396d1eabec2172c0b5a8586d5`의 Quality Gate Run `30251018326`은 SUCCESS다.
- iOS Run `30251018373`은 Build와 새 Deep Link Test를 포함한 UI Test Step이 SUCCESS다.
- 후속 Simulator Step은 17초 안에 Exit 1로 종료했다. C22에서 Shell Deep Link Loop를 제거했으므로 실패는 설치·Route Key 초기화·Home 준비 또는 그 직후 Permission 진입 경계 중 하나다.
- GitHub Check Annotation은 Exit 1만 제공하고 상세 Log API는 현재 인증 만료로 접근할 수 없다.

## 3. 필수 작업

1. `verify-simulator.sh`에 현재 단계 이름을 보유하고 ERR 시 `DAON_SIM_FAILED_STAGE=<allowlisted stage>`와 `DAON_SIM_FAILED_EXIT=<numeric>`만 stderr에 출력하는 진단 경계를 추가한다.
2. 단계는 Boot Status, Install, Initial Terminate, Route Clear, Launch, Home Ready, 각 Permission Phase, Lifecycle Relaunch/Ready, Final Log Scan, Final Terminate처럼 원인을 분리할 수 있어야 한다.
3. 원 명령·원 Exit·`set -euo pipefail`·EXIT Cleanup을 바꾸지 않는다. ERR Trap은 실패를 성공으로 바꾸거나 중복 실행하지 않아야 한다.
4. 단계 표식에는 경로, UDID, URL, 사용자 데이터, Secret, 전체 명령을 출력하지 않는다.
5. Product/XCTest/Native/Bridge/Info/Workflow/Evidence Writer·Wait/Retry·권한·Lifecycle 동작은 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 단계명·ERR 표식·원 Exit 23 보존 Fixture RED
- 구현 후 중간 단계 실패 Fixture가 허용 단계명과 Exit만 출력하고 원 Exit 23으로 종료함을 검증
- 성공 Fixture는 실패 표식 0건
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Simulator Script, 관련 계약 Test, Progress와 Attempt 24 보고서뿐
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속


# R1-M3-06-C02 수정 작업지시서 — 보안 정적 스캔 자기탐지 제거

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `3` |
| 사유 | 어울1 공통 Quality Gate에서 iOS Simulator 검증 Script의 금지 토큰 검사식 자체를 `CLIENT_PUBLIC_INTERNAL_API`로 탐지 |
| 실패보고 | 0회 · 환경·검증 연결 문제이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-3.md` |

원 작업지시·승인 설계·계획·테스트 계획과 C01은 계속 정본이다. 이 수정은 보안 정책이나 기능 범위를 변경하지 않고, 보안 검사 구현이 자신의 금지 문자열 Literal을 Production Script에서 탐지하는 문제만 보정한다.

## 2. 확인된 원인

- `npm run verify:quality-gate`는 33개 검사 중 32개를 통과했다.
- 유일한 실패는 `security-static-scan`의 `CLIENT_PUBLIC_INTERNAL_API`이며 대상은 `apps/mobile/ios/ci/verify-simulator.sh`다.
- 해당 Script는 iOS Binary에 금지된 Browser 공개 내부 API 토큰이 포함되지 않았는지 검사하기 위해 `NEXT_PUBLIC_API_BASE_URL` Literal을 정규식에 직접 포함한다.
- 실제 Client API 호출이나 Binary 검출이 아니라, 검사식 Source가 공통 정적 스캔에 자기탐지된 것이다.

## 3. 필수 수정

1. TDD로 현재 Source에 정확한 금지 토큰이 존재해 공통 보안 스캔에 탐지되는 상태를 RED 증거로 기록한다.
2. `verify-simulator.sh` Source에는 정확한 금지 토큰 Literal이 존재하지 않게 분할 조립한다.
3. 실행 시 조립되는 Binary 검사 Pattern에는 기존 금지 토큰이 그대로 포함되어야 하며, 검사 대상·실패 조건·Exit 동작을 유지한다.
4. 정적 계약 Test에 다음 두 조건을 고정한다.
   - Script Source의 정확한 금지 토큰 Literal 0건
   - 실행 시 조립 Pattern이 기존 금지 토큰을 탐지함
5. 다음 우회는 금지한다.
   - `quality-gate-policy.json` 또는 보안 규칙 약화
   - 제외 경로·예외·Ignore 추가
   - Security Step Skip 또는 성공 강제
   - Binary Scan 삭제·대상 축소·실패 무시
   - 관련 없는 Production·Android·Signing 파일 수정

## 4. 완료 조건

- C02 RED/GREEN 증거와 원인을 Progress·Attempt 3에 기록
- iOS Native Root Gate와 Android Native Gate PASS
- Mobile Type·Lint·Unit·Contract·Bundle 회귀 PASS
- 수정 Script Bash Syntax와 관련 Node Test PASS
- 공통 보안 정적 스캔 또는 동일 실행 경로에서 `CLIENT_PUBLIC_INTERNAL_API` 0건
- `git diff --check` PASS, Android Native Production Diff 0
- 공통 전체 Quality Gate는 어울1이 쓰기 종료 후 재실행
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속

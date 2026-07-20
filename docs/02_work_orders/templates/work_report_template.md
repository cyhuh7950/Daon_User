# 작업 결과보고서 `{work_order_id}` · Attempt `{attempt_no}`

## 판정

`{COMPLETED|FAILURE_REPORT|INCOMPLETE}`

## 판단 이유

- 단일 목표 달성 여부:
- 완료조건별 결과:
- 중대 미진 / 경미 보완:
- 기존 기능 유지 여부와 근거:

## 조치

- 다음 권고: `{ACCEPT|REWORK|RESUME|DESIGN_DECISION|USER_APPROVAL}`
- 남은 작업 또는 Blocker:
- 재개 시 `next_action`:

## 변경과 증거

- 기준 Commit / 종료 Commit:
- 변경 파일:
- 진행 기록:
- 자동 테스트·Build(명령, Exit Code):
- 실제 Process·화면·Network·데이터 검증:
- 미실행 검증과 이유:
- 증거 Manifest:

## 실패 계약

`FAILURE_REPORT`이면 아래를 반드시 채운다.

- `issue_id`:
- 재현 절차와 실제 오류:
- 조사한 원인:
- 시도한 서로 다른 대안과 결과:
- 승인 경계를 넘지 않고 해결할 수 없는 이유:
- 현재 Diff 보존/복구 상태:

`INCOMPLETE`이면 예상치 못한 중단 지점과 마지막 정상 단계, 재개 가능 여부를 명시한다. 단일 명령 실패·권한/도구 문제만으로 정식 실패보고를 만들지 않는다.


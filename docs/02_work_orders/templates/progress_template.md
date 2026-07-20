# 작업 진행·복구 기록 `{work_order_id}`

## 고정 정보

| 필드 | 값 |
| --- | --- |
| issue_id / attempt | `{issue_id}` / `{attempt_no}` |
| 작업지시서 Version / Hash | `{version}` / `{hash}` |
| 기준 Commit | `{baseline_commit}` |
| Writer | 어울2 · `daon-developer` |
| 시작 시각 | `{ISO-8601 with timezone}` |
| 현재 상태 | `IN_PROGRESS` |

## 시작 Snapshot

- `git status --short`:
- 기존 Dirty/Untracked 보존 목록:
- 변경 허용/금지 경로 확인:
- 선행조건 확인:
- 예상 회귀 위험:

## 단계별 기록

아래 블록을 이벤트마다 추가한다. 과거 기록을 덮어쓰지 않는다.

### `{ISO-8601}` · `{stage_id}` · `{STARTED|COMPLETED|ERROR|RECOVERED|TESTED|INTERRUPTED}`

- 수행 내용:
- 변경 파일:
- 실행 명령·Exit Code:
- 검사/테스트 결과:
- 오류·원인:
- 복구·대안:
- 증거 경로:
- 현재 남은 위험:
- `next_action`:

## 종료 Snapshot

- 종료 상태: `{COMPLETED|FAILURE_REPORT|INCOMPLETE}`
- 최종 변경 파일:
- 통과/실패/미실행 검증:
- 작업지시서 밖 변경 0건 확인:
- 결과보고서 경로:
- 재개 시 첫 `next_action`:


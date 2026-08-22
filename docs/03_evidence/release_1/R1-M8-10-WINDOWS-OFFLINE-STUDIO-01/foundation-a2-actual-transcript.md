# Foundation A2 actual Gate transcript

- 실행 시각: 2026-08-14T22:50:00+09:00 이전 완료
- 실행 소스: current Working Tree의 Migration `0016_output_version_content_lineage.py`, `studio_workspace_postgres.py`, `foundation-a2-postgres-gate.sh`
- 자격정보: PostgreSQL password는 실행 프로세스 환경에서만 사용했으며 출력·파일·CLI 인자 저장 0

## RED

실제 PostgreSQL에서 기존 OutputVersion v1은 승인 상태 전이로 `version=5`가 된 뒤 새 내용 Version을 `version=6/state=generating`으로 넣었다. 기존 Canon Trigger는 초기 상태를 `version=1`에서만 허용하여 첫 v2 생성이 `DATABASE_CONSTRAINT_VIOLATION`→`STUDIO_DATABASE_UNAVAILABLE`로 실패했다. 내용 계보 Version과 상태 전이 낙관적 잠금 Version의 혼용이 원인이었다.

## GREEN

Migration 0016은 `content_version`을 내용 계보 전용으로 추가하고 기존 `version`은 상태 전이 전용으로 유지한다. Repository는 최신 Version을 `content_version DESC`로 선택하고, 새 Version은 `version=1/content_version=previous+1`로 만든 뒤 상태를 전이한다. Version과 후속 Action은 advisory lock 후 idempotency replay를 조회한다.

```text
0001 -> 0016 PASS
0016 -> 0015 (empty) PASS
0015 -> 0016 PASS
actual OutputVersion v2 PASS
same-key concurrent requests: one create + one replay PASS
output_versions rows for aggregate: 2
idempotency_records rows for operation/key: 1
RunSnapshot required fields + FK + immutable trigger PASS
cross-workspace RLS visible rows: own only
FK rejection and forced transaction rollback PASS
0016 -> 0015 with multi-content lineage: OUTPUT_VERSION_DOWNGRADE_BLOCKED / SQLSTATE 55000
revision after blocked downgrade: 0016
A2_CLEANUP_REMAINING_0
```

## 자동 회귀

- A2 focused: `21 passed, 2 skipped, 52 subtests passed`
- Migration/Studio/Cloud focused after GREEN: `18 passed, 8 skipped`
- Full API: `381 passed, 27 skipped, 134 subtests passed`

## 판정

immutable Canon, deterministic Version identity, content previous chain, state transition lock, RLS/FK, RunSnapshot, idempotent concurrency replay와 downgrade fail-close가 실제 PostgreSQL에서 닫혔다. 공개 API·DTO 변경은 없다.

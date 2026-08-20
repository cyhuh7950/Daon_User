# Phase E Review1 actual PostgreSQL Gate

- 실행일: 2026-08-20 KST
- 실행기: `scripts/run-phase-e-postgres-gate.sh`
- 격리 경계: disposable database/role suffix `review1_20260820b`
- Migration: fresh empty database에서 `0001`부터 current `0020`까지 순차 적용
- 수집: 18 items, skipped 0, passed 18
- 실행 시간: pytest 2.52s
- 민감정보: DSN/password/raw SQL/raw SQLSTATE 출력 0

## 실제 실행 Test ID

1. `CloudStorageContractTests::test_access_context_rejects_untrusted_or_empty_scope`
2. `CloudStorageContractTests::test_database_errors_are_safe_and_bounded`
3. `CloudStorageContractTests::test_migration_declares_rls_atomicity_and_vector_contract`
4. `CloudStorageContractTests::test_postgres_major_version_range_accepts_packaging_suffix`
5. `CloudStorageContractTests::test_readiness_tracks_the_current_notebook_schema_revision`
6. `PostgresCloudIntegrationTests::test_audit_failure_rolls_back_state_and_idempotency`
7. `PostgresCloudIntegrationTests::test_different_key_same_version_allows_one_winner`
8. `PostgresCloudIntegrationTests::test_notification_audit_and_idempotency_are_atomic`
9. `PostgresCloudIntegrationTests::test_readiness_requires_migration_and_vector`
10. `PostgresCloudIntegrationTests::test_rls_blocks_cross_tenant_and_context_does_not_leak`
11. `PostgresCloudIntegrationTests::test_same_key_concurrency_replays_one_result_and_one_audit`
12. `PostgresCloudIntegrationTests::test_same_tenant_actor_key_isolated_by_workspace`
13. `test_actual_postgres_notebook_replay_scope_metadata_and_rls`
14. `test_actual_postgres_notebook_limit_is_atomic_across_two_connections`
15. `test_actual_postgres_notebook_context_bind_read_scope_and_empty`
16. `test_actual_postgres_title_update_audit_replay_and_failure_are_atomic`
17. `test_actual_postgres_question_result_is_bound_to_selected_notebook_atomically`
18. `test_actual_postgres_source_registration_binds_only_after_canonical_commit`

## 판정

- Notebook create replay/concurrency, FORCE RLS cross-tenant/workspace, selected Context empty/existing, title Audit 원자성, Question 결과 귀속, Source canonical commit 뒤 Binding을 actual PostgreSQL에서 확인했다.
- Source list와 processing status는 selected `notebook_bindings` SQL JOIN/EXISTS 전제이며, unselected/cross-scope가 repository 결과와 storage/detail read 전에 0임을 포함한다.
- 예상 실패는 application Safe error/assertion으로 판정했고 raw SQLSTATE 원문은 Evidence에 기록하지 않았다.
- 종료 trap 결과: `PHASE_E_GATE_CLEANUP db=0 role=0`.

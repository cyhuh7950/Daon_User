# R1-M6-02 TDD Evidence

- RED checkpoint: `1b545a4` — `ModuleNotFoundError: daon_user_api.routing`
- GREEN: `uv run --directory services/api python -m unittest tests.test_routing_egress` → 4 PASS
- Regression: `uv run --directory services/api python -m unittest discover -s tests -p 'test_*.py'` → 159 PASS, 25 SKIP

| 보장 내용 | 테스트 | 결과 |
|---|---|---|
| Local-private에서 외부 Egress를 차단한다 | `test_local_private_blocks_external_egress` | PASS |
| 비용 한도 초과를 Attempt 전에 차단한다 | `test_cost_limit_blocks_before_attempt` | PASS |
| Pinned Route는 두 번째 모델로 자동 Fallback하지 않는다 | `test_frozen_pinned_route_does_not_fallback` | PASS |
| 허용 Route가 Egress·Policy Version·Deployment 계보를 남긴다 | `test_allowed_route_records_egress_and_attempt_lineage` | PASS |

자동 Fallback·외부 Provider 호출·공개 API 확장은 후속 범위다.

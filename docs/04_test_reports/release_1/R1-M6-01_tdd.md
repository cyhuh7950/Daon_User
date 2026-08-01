# R1-M6-01 TDD Evidence

- Work Order: `R1-M6-01-I001`
- Scope: CP3 Core Model Registry·Adapter 내부 계약
- RED checkpoint: `22c0aa1` — `ModuleNotFoundError: daon_user_api.model_registry`
- GREEN validation: `$env:PYTHONPATH='src'; uv run --directory services/api python -m unittest tests.test_model_registry` → `Ran 3 tests ... OK`

| 보장 내용 | 테스트 | 결과 |
|---|---|---|
| Artifact Digest와 Health가 모두 검증된 Deployment만 `ready`가 된다 | `test_ready_deployment_requires_health_and_digest` | PASS |
| Binding은 역할·Data Realm이 다르면 Fail-close한다 | `test_binding_rejects_unhealthy_or_wrong_realm` | PASS |
| Provider가 없는 환경에서 Adapter가 성공으로 가장하지 않고 `NO_AVAILABLE_DEPLOYMENT`를 반환한다 | `test_adapter_fail_closes_without_available_provider` | PASS |

전체 API 회귀: `uv run --directory services/api python -m unittest discover -s tests -p 'test_*.py'` → `Ran 154 tests ... OK (skipped=25)`.

의도적 범위: 실제 Provider 호출, 자동 Routing/Fallback, 공개 Model Deployment Route, Source 이해 Pipeline은 후속 Work Order 범위다.

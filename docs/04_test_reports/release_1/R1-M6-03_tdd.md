# R1-M6-03 TDD Evidence

- RED checkpoint: `1896ae6` — `ModuleNotFoundError: daon_user_api.local_model`
- GREEN: `uv run --directory services/api python -m unittest tests.test_local_model_lifecycle` → 4 PASS
- Regression: `uv run --directory services/api python -m unittest discover -s tests -p 'test_*.py'` → 163 PASS, 25 SKIP

| 보장 내용 | 테스트 | 결과 |
|---|---|---|
| Hardware 부족 상태를 `incompatible`로 판정하고 설치를 차단한다 | `test_hardware_incompatibility_is_fail_closed` | PASS |
| Allowlist·Digest·Signature·License를 모두 검증한다 | `test_manifest_requires_allowlisted_signed_digest_and_license` | PASS |
| Update 실패 시 이전 Ready Version으로 Rollback한다 | `test_install_update_failure_rolls_back_previous_ready_version` | PASS |
| Uninstall 중인 Artifact를 Deployment에 노출하지 않는다 | `test_uninstall_removes_ready_artifact_without_exposing_deployment` | PASS |

실제 모델 다운로드·설치·서명 검증 서비스·Local Node 연결은 후속 환경 검증 범위다.

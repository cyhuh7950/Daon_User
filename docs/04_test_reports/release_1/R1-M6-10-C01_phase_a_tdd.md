# R1-M6-10-C01 Phase A TDD 검증 기록

## 판정

`LOCAL_GREEN` — Provider·Model 설정 기반과 same-origin BFF 계약은 로컬 자동 검증을 통과했다. PostgreSQL Migration, 서버 Build, 실제 Container·Browser 검증 전에는 `DEPLOY_READY` 또는 CP3 완료로 판정하지 않는다.

## RED

- Commit: `137c6a4 test(cp3): add provider BFF route reproducers`
- BFF가 Provider 설정 3종 Route와 쓰기 Method를 허용하지 않아 4개 요청이 404로 실패함을 재현했다.
- 승인 `.env` 이름(`OPENAI_API_KEY`, `OLLAMA_BASE_URL`)을 주입한 Credential 존재 여부 테스트 2건이 `false`로 실패함을 재현했다.

## GREEN

- `python -m unittest discover -s tests -p 'test_*.py'`: 252 passed, 25 skipped.
- Provider 예외 처리기 삽입으로 이동한 회원가입 202 응답을 RED로 재현하고 원래 계약을 복구했다.
- `node --test scripts/tests/provider-settings-web.test.mjs scripts/tests/api-bff-runtime.test.mjs`: 13 passed.
- 배포 Compose의 Provider 환경 전달 계약을 RED로 재현하고 보완한 통합 Node 검증: 14 passed.
- `node scripts/verify-openapi-contract.mjs`: paths 64, operations 90, schemas 87, errors 31, SHA-256 `AE3A0B618C04940158C55E8127E52A8A774A65FD59A4770F42C824C167854B22`.
- `node scripts/lint-workspace.mjs ...`: 3 files passed.

## 보안·운영 계약

- Browser는 `/bff/api/...` same-origin 상대 경로만 호출한다.
- BFF는 고정 내부 Destination, 허용 Route·Method·Query·Header, CSRF와 Session 경계를 유지한다.
- 서버는 API Key 원문을 저장하거나 응답하지 않고 `credential_configured` Boolean만 반환한다.
- Provider URL·Model ID·역할 Mapping·활성 및 선택 상태는 화면과 PostgreSQL에서 관리한다.
- 실제 Key는 배포별 `.env`에서 읽고 Git 산출물에 포함하지 않는다.

## 남은 검증

- ysna-server와 WSL-server의 Migration 0007 사전점검·Backup·적용·재기동.
- 서버 Web Build 및 API 0007 readiness.
- 실제 로그인 Session에서 `/settings/model-connections` 조회·저장과 Browser Network same-origin 확인.

## 실제 PostgreSQL 드리프트 교정

- ysna-server Migration `0006 → 0007` RED: 기존 Canonical `provider_profiles`와 새 설정 Projection 이름이 충돌해 `DuplicateTable`로 안전하게 Rollback됐다.
- 기존 `0003`의 불변 Canonical Entity·FK·RLS·Trigger는 변경하지 않았다.
- 신규 mutable 설정 저장소를 `provider_setting_profiles`, `provider_setting_deployments`, `provider_setting_role_bindings`로 분리했다.
- 로컬 회귀: API 252 passed·25 skipped, Node 14 passed, 예약 테이블 SQL 참조 0건.

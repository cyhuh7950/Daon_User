# R1-M5-01-C01 로컬 검증 Evidence

- TDD RED: DB 부재 시 Dependency build 10초 후 `PoolTimeout`, 느린 Readiness의 Event Loop 0.279초 차단, Workspace 없는 Idempotency PK를 각각 재현했다.
- TDD GREEN: DB 부재 Dependency build 제한 시간 내 완료, 동시 Live 0.15초 이내, Workspace 포함 PK 계약 3/3 PASS.
- API 전체: 93개 중 82 PASS, 격리 PostgreSQL·POSIX 전용 11 SKIP.
- 직접 검증: Audit 13, Identity 18, Authorization 22, Runtime 13, Notification 10, Cloud 11(로컬 실DB 7 SKIP) 및 실제 API/BFF Process PASS.
- Web Production Build, Ruff, strict mypy, pip-audit(알려진 취약점 0), Repository 독립성(위반 0), Toolchain PASS.
- 최종 공통 Quality Gate: lint 8, type 5, unit 9, contract 3, build 8, security 3, independence 1 — 총 37개 PASS, Exit 0.
- Quality 생성 기준선 Evidence와 `.coverage`는 복구·제거했고 Tauri `gen/`은 종료 시 자체 정리되어 부재했다.
- uv 공유 환경을 병렬 사용해 발생한 Lock 오류와 최초 Quality의 Sandbox `EPERM`은 순차·승인 실행으로 복구한 환경 중단이며 제품 실패가 아니다.

비밀값, 연결 문자열, DB Host, SQL과 원시 Credential 출력은 Evidence에서 제외했다.

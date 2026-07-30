# R1-M5-04 서버·Local 검증 요약

- 검증 SHA: `fff34959a24e2963f3003e4cb6a378c045a9b31b`
- 격리 경계: `/home/ubuntu/deploy/daon-user/R1-M5-04-C01`, Compose label `daon_r1_m5_04_c01_e0dde27`
- 환경: aarch64, PostgreSQL `18.4`, pgvector `0.8.2`, Python `3.14.3`
- Migration: 빈 DB `0001→0002→0003`, Head 재적용, `0003→0002→0003`, 적용 전 Backup 복구 후 `0003` 재적용 모두 통과
- Schema: 설계 Entity 52개, 신규 강제 RLS Table 52개, Tenant/Workspace 포함 FK 175개, 승인 전이 규칙 83개
- 실제 `daon_app`: 승인 전이 83/83 실행. GenerationRequest `confirmed→configuring`과 `submitted` 이후 전이, stale version, missing/cross-scope record, 직접 Update/Delete, 잘못된 Digest를 모두 거부
- Attempt/Audit: 성공·거부 모두 Commit 후 불변 `canon_transition_attempts`와 연계 Audit를 1건씩 보존. 동일 Attempt 동시 재전송은 결과 2개/Ledger 1건, 서로 다른 Attempt의 동일 Version 동시 요청은 성공 1/`CANON_VERSION_CONFLICT` 1로 검증
- 계보: SourceVersion/ProcessingRun, 문서·오디오·retry, RunSnapshot/RunResult/Citation과 고정 SourceVersion 연결을 실제 DB에서 확인
- C01 영향 회귀: Cloud/Data Canon Domain·Contract·Repository 실제 DB 17/17 무 Skip. 선행 Object/Queue DB·계약 14/14와 S3 미구성 2 Skip 결과는 변경하지 않음
- 실제 Service: 비밀을 읽거나 변경하지 않은 격리 development API에서 `/health/live` 200, `/health/ready` 200, Container Restart 후 `/health/ready` 200. PostgreSQL 기능은 별도 실제 `daon_app` Session으로 검증
- Local 암호화 Projection: 전체 88 PASS/1 플랫폼 Skip, Restart·불변·Workspace/Area 격리·평문 Canary 0·외부 Network 시도 0
- Web: Production Build/TypeScript PASS, Workspace Lint PASS, 독립성 `violations=0`
- Quality Gate: SHA `fff3495`에서 37개 검사 모두 PASS, 실패 0. 이후 제품 코드는 바뀌지 않았고 Evidence·진행 기록만 갱신했다.
- 정리: 신산님의 명시 승인 후 정확한 C01 Checkout/Container/Network/Volume을 정리해 `ROOT=0 C=0 N=0 V=0`을 확인했다. `shared-db` `aac57a5a1b7f`, `netdata` `8cab33d7f80b`, `nginx-proxy-manager` `337026a012e2`, `proxy-network` `ba743b3e6a37`은 Before/After 동일하다.

전체 API Server Suite의 1회 실행에서 Python 전용 Runner Image에 Node/Next가 없어 Process Launcher 1건이 환경 오류였으나, 같은 Test는 Node가 있는 로컬 전체 API Suite에서 통과했고 서버 POSIX Process 4건은 통과했다. 제품 결함으로 분류하지 않았다.

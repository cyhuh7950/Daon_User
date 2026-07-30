# R1-M5-04 서버·Local 검증 요약

- 검증 SHA: `d851acbf2974ea0e26dfb884d88b601054bd9031`
- 격리 경계: `/home/ubuntu/deploy/daon-user/R1-M5-04`, Compose label `daon_r1_m5_04_6682f43`
- 환경: aarch64, PostgreSQL `18.4`, pgvector `0.8.2`, Python `3.14.3`
- Migration: 빈 DB `0001→0002→0003`, Head 재적용, `0003→0002→0003`, 적용 전 Backup 복구 후 `0003` 재적용 모두 통과
- Schema: 설계 Entity 52개, 신규 강제 RLS Table 51개, Tenant/Workspace 포함 FK 174개, 전이 규칙 84개
- 실제 `daon_app`: 허용 전이 84/84 실행, 불법 전이·stale version·직접 Update/Delete·잘못된 Digest·교차 Workspace/Tenant 관계 모두 거부
- 계보: SourceVersion/ProcessingRun, 문서·오디오·retry, RunSnapshot/RunResult/Citation과 고정 SourceVersion 연결을 실제 DB에서 확인
- 영향 회귀: Cloud/Data Canon 15/15, Object/Queue DB·계약 14/14 통과. S3 Endpoint가 없는 이 Fixture에서 MinIO 전용 2건만 명시적으로 Skip
- 실제 Service: `/health/live` 200, `/health/ready` 200, Container Restart 후 `/health/ready` 200
- Local 암호화 Projection: 전체 88 PASS/1 플랫폼 Skip, Restart·불변·Workspace/Area 격리·평문 Canary 0·외부 Network 시도 0
- Web: Production Build/TypeScript PASS, Workspace Lint PASS, 독립성 `violations=0`
- Quality Gate: SHA `89f42e1`에서 PASS. 이후 최종 SHA까지 제품 코드는 바뀌지 않았고 Local Network 0 검증 Test와 진행 기록만 추가한 뒤 관련 Test를 재실행했다.
- 정리: Checkout 0, Container 0, Network 0, Volume 0. `shared-db`, `netdata`, `nginx-proxy-manager` Before/After 동일.

전체 API Server Suite의 1회 실행에서 Python 전용 Runner Image에 Node/Next가 없어 Process Launcher 1건이 환경 오류였으나, 같은 Test는 Node가 있는 로컬 전체 API Suite에서 통과했고 서버 POSIX Process 4건은 통과했다. 제품 결함으로 분류하지 않았다.

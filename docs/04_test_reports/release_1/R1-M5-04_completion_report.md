# R1-M5-04 완료보고

## 판정

`COMPLETED`

## 판단 이유

- 상세 설계 §16의 Entity 52개를 `data_canon_manifest`로 1:1 추적하고, PostgreSQL `0003`의 전용 관계형 Table·복합 FK·Unique·Index·강제 RLS로 구현했다.
- SourceVersion부터 Processing/Evidence/Index, Routing/Run/Citation, Studio/Approval/Delivery/KnowledgeRegistration까지 계보를 구조적으로 연결했다.
- Snapshot·Version·Attempt·Evidence·Ledger는 DB Trigger와 최소 권한으로 제자리 Update/Delete를 거부한다. Canonical JSON Object·정규 문자열·SHA-256 Digest·Previous Version chain도 DB에서 검증한다.
- GenerationRequest의 비승인 `confirmed→configuring` 역전이를 제거하고 `configuring→confirmed→submitted` 단방향 계약을 유지했다. 7개 Entity의 승인 전이 83개를 실제 `daon_app` Session으로 전부 검증했다.
- 성공·불법 전이·Version 충돌·missing/cross-scope 거부를 Commit 가능한 구조화 결과로 처리해 Attempt/Audit Ledger를 보존하고, Repository가 Commit 이후 안정 Error로 변환한다. 동일 Attempt 동시 재전송도 Ledger 1건으로 직렬화했다.
- M5-03 SQLCipher 저장소에 Cloud와 동일한 ID·Version·schema_version·digest·created_at·previous_version 의미의 `local_private` Envelope를 추가했다. Cloud 조직·승인·Provider Secret 복제와 Sync/Copy/Publish는 구현하지 않았다.
- 실제 PostgreSQL 18.4 Migration/Backup/Rollback/Restore, Local 암호화 DB Restart, Service Health와 회귀 검증 근거가 모두 있다.

## 생성·변경 결과

- Architecture: `docs/03_architecture/data_canon_manifest.json`
- Migration: `services/api/migrations/versions/0003_data_canon_lineage.py`
- Cloud Domain/Repository: `services/api/src/daon_user_api/data_canon.py`
- Readiness Head: `services/api/src/daon_user_api/cloud_storage.py`, 관련 회귀 Test
- Local Projection: `services/local-service/src/daon_user_local_service/local_storage.py`
- Tests: API Contract/Domain/PostgreSQL, Local 암호화/격리/Network 0, 서버 전 범위 검증기
- Evidence: `docs/03_evidence/release_1/R1-M5-04/manifest.json`, `server-validation.py`, `server-validation-summary.md`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M5-04_progress.md`

원 구현 Commit은 RED `f837ae3`, 구현 `6682f43`, 서버 검증기 `937ceb0`, Readiness 회귀 `89f42e1`, Local Network 0 검증 `d851acb`로 분리했다. C01은 RED `87fcc00`, 구현 `e0dde27`, 동시성 검증기 `fff3495`로 분리했다.

## 테스트 결과

- PostgreSQL 18.4: Entity 52, 강제 RLS Table 52, Scope FK 175, 승인 전이 83/83 PASS
- Attempt/Audit: 성공·거부 Commit 보존, 동일 Attempt 동시 결과 2/Ledger 1, 서로 다른 Attempt 동시 성공 1/Version Conflict 1 PASS
- Migration: `0001→0002→0003`, 재적용, `0003→0002→0003`, 사전 Backup Restore 후 재검증 PASS
- 실제 DB C01 영향 Suite: 최종 제품 SHA `fff3495` Cloud/Data Canon Domain·Contract·Repository 17/17 PASS, SKIP 0. 선행 Object/Queue 14/14 PASS와 S3 전용 2 SKIP은 회귀 경계가 바뀌지 않음
- Local: 88 PASS/1 플랫폼 Skip, 평문 Canary 0, 외부 Network 시도 0, 변경범위 Mypy/Ruff PASS
- API 로컬 전체: 117건 중 96 PASS/21 환경 Skip. API 서버 전체의 Node 전용 1건 환경 오류는 로컬 Node 환경 PASS, 서버 POSIX Process 4 PASS로 분리 검증
- 실제 Service: live 200, ready 200, Restart ready 200
- Web Build/TypeScript PASS, Workspace Lint PASS, 독립성 위반 0, SHA `fff3495` Quality Gate 37/37 PASS
- 서버 정리: 신산님의 명시 승인 후 C01 `ROOT=0 C=0 N=0 V=0`. 보호 Container `shared-db`·`netdata`·`nginx-proxy-manager`와 `proxy-network` ID Before/After 동일

## 기존 기능 유지·영향 범위

- 기존 Auth·Authorization·Audit·Notification·Object/Queue·Tauri 공개 API는 변경하지 않았다.
- `cloud_storage`의 기대 Migration Head만 승인된 `0003`으로 갱신했다.
- Browser 코드와 API 경로는 수정하지 않아 same-origin/BFF 경계 변화가 없다.
- Local 기존 Object/File/Vector Key·Cipher·Loopback·Workspace/Area 계약을 유지하고 별도 불변 Table만 추가했다.

## 미해결 사항

- 전체 Local Mypy에는 작업 전부터 존재한 `test_main.py`의 명시적 Export 9건 오류가 남아 있다. 이번 변경 파일 2건의 엄격 Mypy는 PASS이며 범위 밖 기존 Test를 수정하지 않았다.
- S3 전용 2건은 이번 DB-only Fixture에서 Skip했으며 선행 M5-02 증거와 이번 Object/Queue DB 회귀를 재사용했다.
- 공식 Working Tree에는 외부 검증자/사용자 산출물로 보이는 untracked `interim_review_2026-07-30.md`, `release_1_model_provider_queries.md`가 남아 있다. 본 작업에서 수정·삭제·Stage·Commit하지 않았다.

## 조치

- 어울1이 Commit/Evidence와 외부 독립 검증 결과를 검토해 최종 완료를 판단한다.
- 제품 Gate 또는 다음 Checkpoint 진입 여부는 신산님의 Go/No-Go 판단을 따른다.
- 정식 `FAILURE_REPORT`는 0회다.

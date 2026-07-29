# R1-M5-02 완료보고

## 판정

`COMPLETED` — 보정 Work Order `R1-M5-02-C01`에서 두 Object 통합시험 Fixture 계열을 계약에 맞게 최소 교정했고, 로컬 Quality Gate와 ysna-server exact SHA의 PostgreSQL 18.4·MinIO·실제 API/Worker·장애/복구·Rollback/Restore 검증을 완료했다.

## 판단 이유

- Replay는 Object·Job·Event ID를 재사용하면서 `replayed`만 `false → true`가 되는 계약을 분리 검증한다. Entity·Attempt 중복 0과 요청 Audit 1건도 확인한다.
- Claim·Crash·Retry는 DB가 저장한 `next_attempt_at`을 기준으로 사용한다. Retry Fixture는 Coordinator가 기록한 Job `max_attempts`와 Worker Policy를 동일한 2회 계약값으로 맞췄다.
- 제품 코드·공개 API·Schema·Migration·보안/데이터 계약·Dependency·Compose는 변경하지 않았다.
- exact SHA `f3da3c78a3fce3abecf94dff932df3cdb66d53d3` 서버 검증에서 Object `16/16`, Cloud `11/11`, Runtime `15/15`, Restore 후 Object `16/16`이 모두 통과했다.

## 조치와 결과

### 변경 파일

- `services/api/tests/test_object_queue.py`: Replay 비교, Audit 중복, DB 시간 경계, Retry Job/Worker 시도 한도 Fixture 교정
- `docs/03_evidence/release_1/R1-M5-02-C01/*`: 재현 가능한 서버 검증 Script·요약·Manifest
- `docs/04_test_reports/release_1/R1-M5-02-C01_progress.md`: 단계별 복구 기록
- `docs/04_test_reports/release_1/R1-M5-02_progress.md`: 공식 작업 위치·완료 단계 정합화
- 이 완료보고

### 로컬 검증

- Object 직접 Test: 7 PASS·9 환경 SKIP, 실패 0
- Ruff: PASS
- strict Mypy: Object/Worker 2 Source, 무오류 PASS
- Cloud: 4 PASS·7 환경 SKIP, 실패 0
- Runtime: BFF 10 PASS, API 15 PASS, Lifecycle 2 PASS·4 POSIX SKIP, Production Web Build·실제 API/Next Process PASS
- 공식 Quality Gate: 7 Category·37 Check PASS, Security·Independence 포함, 실패 0

### ysna-server 검증

- exact SHA·detached clean·ARM64 binding PASS
- PostgreSQL 18.4 Preflight, pgvector 0.8.2, Application Role 최소권한 PASS
- Migration `0002` 적용·재적용, Backup, downgrade base, Restore, upgrade head PASS
- Object 16/16, Cloud 11/11, Runtime 15/15, Restore 후 Object 16/16 PASS
- 실제 API Object/DB 장애 시 live 200·ready 503, 복구 후 ready 200
- Worker와 API SIGTERM Exit 0
- C01 소유 자원 잔여 0, 공용 자원 불변

## Git·잔여 상태

- Fixture Commit: `a7c0fd9`, 추가 Retry Fixture Commit: `f3da3c7`
- 정식 `FAILURE_REPORT`: 0회
- 미해결 제품 결함: 0건
- 문서·Evidence-only Commit은 이 보고와 함께 별도 기록한다.

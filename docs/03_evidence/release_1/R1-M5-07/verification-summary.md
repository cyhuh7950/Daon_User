# R1-M5-07 로컬 검증 요약

## 판정

`EXTERNAL_DATA_PASS / BROWSER_EVIDENCE_PENDING`

## 로컬 검증 근거

- API 전체: `147 passed`, `23 skipped`.
- Local Service 전체: `96 passed`, `1 skipped`.
- Web Recovery 계약: `27 passed`.
- Workspace Lint: `PASS`.
- Web Production Build: Compile·TypeScript·Static Page `8/8` 통과.
- OpenAPI: `64 paths`, `89 operations`, `80 schemas`, SafeError `31`로 신규 공개 오류 코드 0건.
- Quality Gate: `395.8초`, exit `0`, 7개 범주 모두 `PASS`, failure 0.
- ysna-server C01: PostgreSQL `18.4`, Migration `0001→0006`, 재적용, 빈 C02 DB `0006→0005→0006`, RLS·FK·Trigger 검증 PASS.
- 실제 PostgreSQL·MinIO Integration: `3 tests OK`; Backup/Restore Restart 영속화, 최소권한 RLS·Cross-scope·Checksum/누락·Fixture-only Restore PASS. DB 조회 시 Revision `0006`, `backup_records=1`, `restore_requests=1`, Locator `1`.
- 전용 Bucket `daon-r1-m5-07-c01-8c8a40c`에 Source·Target Fixture가 존재하며 테스트 컨테이너는 자동 제거됐다.

Cloud HTTP 검증은 승인된 7개 Operation, 현재 권한, Preview와 Execute의 서로 다른 Step-up, If-Match, Idempotency, fixture-only 목적지와 원본 변경 0건을 포함한다. Local HTTP 검증은 승인된 3개 Route, command-bound token, 암호화 상태 저장, Restart, Repair와 `manual_recovery_required`를 포함한다.

## 미수행 외부 검증

- 실제 Web·Windows 화면 및 Browser Network same-origin 검증.
- ysna-server Web/API 서비스 기동과 실제 HTTP Browser Network 검증.

위 Browser 검증만 C01 Compose가 DB·MinIO만 포함해 아직 수행하지 못했다. 외부 데이터 검증은 통과했으며 운영 데이터 Restore·제자리 덮어쓰기·파괴적 손상 주입은 0건이다.

## 보호 확인

- 과거 `R1-M1-05` Quality Gate 산출물과 `R1-M4-01` OpenAPI Evidence는 HEAD로 복원했다.
- 보호 Untracked 2건은 수정·삭제·Stage하지 않았다.
- `D:\Project\Daon_User`, `C:\tmp`, ysna-server와 외부 자원은 변경하지 않았다.
- C01 전용 DB·Bucket·Fixture는 신산님 승인에 따라 검증 완료 후 유지한다.

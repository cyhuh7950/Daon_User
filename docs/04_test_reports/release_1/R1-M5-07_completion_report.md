# R1-M5-07 구현·검증 보고서

## 판정

`VERIFYING` — 로컬 구현·자동 회귀·Quality Gate와 ysna-server PostgreSQL 18.4/RLS·MinIO 실제 데이터 검증은 통과했다. 실제 Web/Windows 화면과 Browser Network 증거가 남아 있어 최종 `COMPLETED`는 보류한다.

## 판단 이유

- Cloud Backup/Restore Domain, Migration `0006`, 승인된 공개 API 7개와 기존 SafeError 안전 매핑을 구현했다.
- Preview와 Execute의 권한·Step-up을 분리하고, 현재 Retention/Hold/Tombstone 우선, Purged Content 부활 0, Fixture 격리와 원본 변경 0을 자동 검증했다.
- Local SQLCipher 상태 Chain과 승인된 Loopback API 3개를 구현하고 Restart·Journal 누락·Checksum 불일치·Repair·`manual_recovery_required`를 검증했다.
- Web same-origin Adapter와 운영 화면의 요청·목록·Preview·진행·결과 UI를 연결했다. Server/Client 경계는 Client Wrapper로 고정했고 Production Build를 통과했다.
- API 147건, Local 96건, Web 27건, OpenAPI·Lint·Build와 최종 Quality Gate 7개 범주가 통과했다.
- 과거 Evidence 정본은 변경하지 않았고 현재 OpenAPI 요약을 R1-M5-07 Evidence Pack에 분리했다.
- ysna-server C01 전용 환경에서 Migration·RLS·MinIO·실제 Backup/Restore Integration을 수행했다. DB `daon_r1_m5_07_c02`는 Revision `0006`, Backup 1건·Restore 1건·Locator 1건이며 Integration 3건이 통과했다.
- 실제 Web/Windows 화면과 Browser Network 검증은 현재 C01 Compose가 DB·MinIO만 기동해 남아 있다. 이 증거가 확보되기 전에는 작업지시서에 따라 `COMPLETED`로 보고하지 않는다.

## 조치

- 로컬 구현 Commit `d47de39d07d4e336d59b7f186b48c847204e4c8a`과 Evidence Commit을 `codex/r1-m5-07`에 Push한 뒤 쓰기를 중지한다.
- 어울1은 Web/API 서비스 기동 또는 별도 운영형 Browser 검증 경로를 확정한 뒤 실제 화면·same-origin Network 증거를 수집한다.
- Browser 증거 수집 후 Evidence Manifest와 이 보고서의 최종 판정을 갱신하고 신산님에게 Go/No-Go를 요청한다.

## 변경 결과

- Cloud: Backup/Manifest/Restore Preview·Request·Verification Schema, Domain Service, 7개 Runtime Route, OpenAPI.
- Local: 암호화 Append-only Recovery 상태, Restart/Repair Service, command-bound Loopback 3개 Route.
- Web: same-origin Recovery API Adapter, Operations 실제 API 패널, Client 경계 Wrapper.
- Test/Evidence: Domain·Contract·HTTP·암호화 Restart·Web Adapter·전체 회귀와 R1-M5-07 OpenAPI 요약.

## 외부 검증 재개 증거 (2026-07-31)

- C01 원격 repo는 exact `bc139fa6303657d9f1de8431e89a5caf4df758ef` detached clean 상태다.
- 전용 PostgreSQL 컨테이너는 `pg_isready` 통과, `server_version=18.4`, C02 DB revision `0006`이다. `daon_app`은 superuser·bypassrls·login이 모두 false다.
- C02 DB catalog에서 대상 RLS forced, 정책 94건, non-internal trigger 128건을 확인했고 최소권한 context에서 `backup_records` 교차 노출 0건을 확인했다.
- 전용 MinIO는 내부 `mc ready` 통과했으나 초기 Bucket이 없어 사용자 승인 후 지정 Bucket `daon-r1-m5-07-c01-8c8a40c`를 생성했다. private 정책으로 유지하고 Fixture object `source/fixture-c02-object-001` (24 bytes, SHA-256 `8a82cd795775d6157eafa4b4efca99ef35212ce63ced19cfea5f8f51e272dedb`)을 seed했다.
- API/Restart 통합 실행기는 원격 호스트에 준비되어 있지 않아 실제 7종 API·Restart·Manifest Restore 실행은 아직 미완료다. 따라서 최종 판정은 계속 `BLOCKED`이며 Formal Failure 0회다.

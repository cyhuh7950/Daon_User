# R1-M5-07 구현·검증 보고서

## 판정

`BLOCKED` — 승인된 로컬 구현·자동 회귀·Quality Gate는 통과했으나, 완료 필수 조건인 PostgreSQL 18.4/RLS·MinIO·실제 화면/Browser Network·ysna-server 통합 증거는 별도 승인 전 수행할 수 없다.

## 판단 이유

- Cloud Backup/Restore Domain, Migration `0006`, 승인된 공개 API 7개와 기존 SafeError 안전 매핑을 구현했다.
- Preview와 Execute의 권한·Step-up을 분리하고, 현재 Retention/Hold/Tombstone 우선, Purged Content 부활 0, Fixture 격리와 원본 변경 0을 자동 검증했다.
- Local SQLCipher 상태 Chain과 승인된 Loopback API 3개를 구현하고 Restart·Journal 누락·Checksum 불일치·Repair·`manual_recovery_required`를 검증했다.
- Web same-origin Adapter와 운영 화면의 요청·목록·Preview·진행·결과 UI를 연결했다. Server/Client 경계는 Client Wrapper로 고정했고 Production Build를 통과했다.
- API 147건, Local 96건, Web 27건, OpenAPI·Lint·Build와 최종 Quality Gate 7개 범주가 통과했다.
- 과거 Evidence 정본은 변경하지 않았고 현재 OpenAPI 요약을 R1-M5-07 Evidence Pack에 분리했다.
- 외부 배포·DB Migration·MinIO·실제 Browser 검증은 승인되지 않았으므로 수행하지 않았다. 이 상태에서는 작업지시서에 따라 `COMPLETED`로 보고할 수 없다.

## 조치

- 로컬 구현 Commit `d47de39d07d4e336d59b7f186b48c847204e4c8a`과 Evidence Commit을 `codex/r1-m5-07`에 Push한 뒤 쓰기를 중지한다.
- 어울1은 신산님에게 ysna-server 격리 배포·PostgreSQL 18.4/RLS·MinIO·실제 Browser/Windows 검증 승인을 요청한다.
- 승인 후 작업지시서의 외부 검증을 수행하고 Evidence Manifest의 미검증 항목을 실제 근거로 갱신해야 최종 완료 판단이 가능하다.

## 변경 결과

- Cloud: Backup/Manifest/Restore Preview·Request·Verification Schema, Domain Service, 7개 Runtime Route, OpenAPI.
- Local: 암호화 Append-only Recovery 상태, Restart/Repair Service, command-bound Loopback 3개 Route.
- Web: same-origin Recovery API Adapter, Operations 실제 API 패널, Client 경계 Wrapper.
- Test/Evidence: Domain·Contract·HTTP·암호화 Restart·Web Adapter·전체 회귀와 R1-M5-07 OpenAPI 요약.

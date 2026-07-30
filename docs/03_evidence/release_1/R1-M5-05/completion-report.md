# R1-M5-05 완료보고

## 판정

`COMPLETED`

## 판단 이유

- 구현 기준 SHA `365015a70e47b001573d04baccb36f66ac05822f`에서 승인된 Preview → Step-up 승인 → 승인 항목 Batch 재개 → 명시 Conflict 해결 계약을 로컬과 ysna-server 격리 환경에서 검증했다.
- 서버 실행은 신산님의 명시 승인 아래 어울1이 직접 수행했다. 어울2는 서버 실행자로 주장하지 않고 전달받은 검증 사실을 Evidence와 진행 기록으로 정리했다.
- 실제 PostgreSQL `18.4`·pgvector `0.8.2`, `daon_app` 최소권한, Migration `0001→0004`, 강제 RLS·불변·Lost Update·Object Queue와 실제 Runtime API Ready/5개 Sync Path가 통과했다.
- 최종 API `126/126`, Linux Local `89 PASS / 2 platform SKIP`, OpenAPI·Workspace Lint·Repository Independence가 통과했다.
- Formal Failure Report는 `0`회다.

## 사실관계 정정

과거 진행 기록의 “서버 Checkout이 이미 존재했다”는 진술은 라이브 확인과 불일치했다. 실제 초기 상태에는 Checkout이 없었고, 어울1이 public GitHub에서 Branch를 Clone해 다음 exact SHA/clean Checkout을 생성했다.

- Checkout: `/home/ubuntu/deploy/daon-user/R1-M5-05-C01/365015a70e47b001573d04baccb36f66ac05822f`
- SHA: `365015a70e47b001573d04baccb36f66ac05822f`

이는 증거 정확성 정정이며 제품 결함이나 정식 `FAILURE_REPORT`가 아니다.

## 검증 결과

### 정적·로컬 Windows

- API 전체: `126 PASS / 22 external-platform SKIP`
- Local-service 전체: `90 PASS / 1 platform SKIP`
- Workspace Lint: `16 files PASS`
- OpenAPI: `52 paths`, `76 operations`, `69 schemas`, `28 errors`, 계약 SHA-256 `F7CE2D661F34A63080ADDBE4E0194898D5253D1E37030836E75535E9D6BD7E14`
- Repository Independence: `8 components`, `10 edges`, `10 package files`, 로컬 검증 시 위반 `0`

### ysna-server 실제 PostgreSQL·API

- 환경: PostgreSQL `18.4`, pgvector `0.8.2`, Node `24.18.0`, npm `11.12.1`, uv `0.11.2`, Python `3.14.3`
- Migration: 빈 DB `0001→0004 PASS`
- 최소권한: 실제 `daon_app PASS`
- Sync PostgreSQL: `1/1 PASS`
- Sync Contract/Domain/Runtime/HTTP: `8/8 PASS`
- Cloud/RLS: `11/11 PASS`
- Object Queue: `16/16 PASS`
- Canon: `4/4 PASS`
- 실제 API `python -m daon_user_api.main`: `RUNTIME_READY=200`, `SYNC_PATHS=5`, 정상 종료
- 최종 API Full: `126/126 PASS`
- 최종 Independence: `8 components`, `10 edges`, `10 package files`, `205 scanned files`, `0 violations`

### Linux Local 암호화 저장소

- 최종: `89 PASS / 2 platform SKIP`
- 암호화 Queue·재시작 검증은 통과했고 플랫폼 전용 2건만 Skip했다.

## 오류와 복구

- 첫 Restore는 Test 실행 뒤 생성한 Dump에 Fixture가 포함되어 Cloud Constraint Test 5건이 실패했다. Clean DB 재생성 → Migration → `clean-post-0004.dump` → Restore/Restart 후 Sync `1/1`, Cloud `11/11`, Object `16/16`, Canon `4/4`가 다시 통과했다. 제품 결함이 아니다.
- Linux Local 첫 실행은 Readonly Checkout에서 `TEMP=.`를 사용해 1건이 exit `67`로 실패했다. 격리된 쓰기 가능 임시 경로로 재실행해 통과했다.
- Python-only Runner의 Node 부재, Node+uv Runner의 Next CLI 부재는 검증 Wrapper 환경 문제였다. 요구 Toolchain과 임시 Workspace `npm ci`로 전체 API를 통과했다.
- tmpfs의 Native TypeScript 실행 `EACCES`와 Node 22 Engine 불일치는 요구 버전을 낮추지 않고 Node `24.18.0`/npm `11.12.1` 일반 Ephemeral Rootfs로 재실행해 해결했다.

## 보호 경계와 보존 자원

- `shared-db`, `netdata`, `nginx-proxy-manager` Container ID·이름 Before/After 불변
- `proxy-network` 불변
- `common_default` Before/After 모두 없음
- Validation Container 잔존 `0`
- `PROTECTED_RESOURCES_UNCHANGED=true`
- `SERVER_ALL_GATES_OK`

삭제 승인이 없으므로 다음 R1-M5-05 격리 자원은 보존했다.

- Compose Project: `daon_r1_m5_05_c01_365015a`
- Database: `daon_r1_m5_05_c01`
- Object Bucket: `daon-r1-m5-05-c01-365015a`
- Database Container: Healthy
- Object Storage Container: Up
- 전용 Network: 1
- 전용 Volume: DB/Object 2
- Checkout·Runtime 경로

물리적 Cleanup은 신산님의 별도 승인 대상이다.

## 미수행·별도 Gate

- Browser 코드는 변경하지 않아 Browser/Network 검증은 수행하지 않았다.
- Release Test Plan의 TP Wave·Go/No-Go 결정은 이번 구현 완료와 별도다.
- OCI 운영 배포는 승인 범위 밖이라 수행하지 않았다.
- 격리 자원 Cleanup은 수행하지 않았다.

## 조치

구현과 필수 운영 증거는 완료됐다. 다음 판단은 격리 자원 Cleanup 승인 여부 및 Release Test Plan의 해당 TP Wave 진입 여부다.

`COMPLETED | R1-M5-05 | 승인된 Sync Copy/Publish·재개·Conflict 계약 구현과 증거 정리 | Migration 0004, 5개 API, PostgreSQL/Object Queue, 암호화 Local Queue, Evidence | 로컬·서버 필수 Gate PASS, Formal Failure 0 | 브라우저·TP Wave·OCI·Cleanup 미수행 | 격리 자원 Cleanup과 TP Wave 진입은 신산님 별도 판단`

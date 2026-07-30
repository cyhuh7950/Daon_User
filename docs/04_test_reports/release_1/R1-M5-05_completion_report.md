# R1-M5-05 완료보고 정본

## 판정

`COMPLETED`

## 판단 이유

- 구현 기준 SHA `365015a70e47b001573d04baccb36f66ac05822f`에서 승인된 Preview → Step-up 승인 → 승인 항목 Batch 재개 → 명시 Conflict 해결 계약을 로컬과 ysna-server 격리 환경에서 검증했다.
- 서버 실행은 신산님의 명시 승인 아래 어울1이 직접 수행했다. 어울2는 전달받은 검증 사실과 산출물만 정리했다.
- PostgreSQL `18.4`·pgvector `0.8.2`, `daon_app` 최소권한, Migration `0001→0004`, RLS·불변·Lost Update·Object Queue와 실제 Runtime Ready/5개 Sync Path가 통과했다.
- 최종 API `126/126`, Linux Local `89 PASS / 2 platform SKIP`, OpenAPI·Workspace Lint·Repository Independence가 통과했다.
- 어울1의 첫 산출물 검토는 완료보고 정본 경로와 Manifest 필수 연결 누락으로 `INCOMPLETE 1회`였으며, 구현·서버 검증 결함이나 정식 `FAILURE_REPORT`는 아니다. 이번 C02에서 문서만 보완했고 Formal Failure Report는 `0`회다.

## 사실관계 정정

과거 진행 기록의 “서버 Checkout이 이미 존재했다”는 진술은 라이브 확인과 불일치했다. 실제 초기 상태에는 Checkout이 없었고, 어울1이 public GitHub에서 Branch를 Clone해 다음 exact SHA/clean Checkout을 생성했다.

- Checkout: `/home/ubuntu/deploy/daon-user/R1-M5-05-C01/365015a70e47b001573d04baccb36f66ac05822f`
- SHA: `365015a70e47b001573d04baccb36f66ac05822f`

이는 증거 정확성 정정이며 제품 결함이나 정식 `FAILURE_REPORT`가 아니다.

## 필수 계약별 검증 연결

| 필수 항목 | 판정 | 실제 근거 | 확인 결과·경계 |
| --- | --- | --- | --- |
| 승인 Snapshot·RLS | PASS | 서버 Sync PostgreSQL `1/1`, Cloud/RLS `11/11`, Clean Restore 후 동일 재통과 | 승인 Snapshot·Manifest·Batch·Target·Reindex 행이 Scope 안에서 1건씩 영속화 |
| Step-up | PASS | Runtime HTTP `1/1`, Domain 무승인/Scope Test `1/1`, 서버 Sync Group `8/8` | 잘못된 Step-up은 `403`; Operation에 결합한 1회용 Step-up 뒤에만 승인·전송 |
| Batch Idempotency·Resume | PASS | Domain Idempotency/Resume/Lost Update `1/1`, PostgreSQL Restart Projection `1/1` | 동일 Key는 Batch·전송 중복 0; Cursor 재개는 남은 승인 Item만 전송 |
| Local Encryption·Restart | PASS | Local Sync Queue `2/2`, Linux Local `89 PASS / 2 platform SKIP` | Restart 후 암호화 Metadata 복구, Operation/Cursor 평문 Canary `0` |
| Reconnect | 승인 범위 PASS | 승인 항목 재개 `1/1`, Offline Lock/Network `1/1`, 부분 Batch 재개 `1/1` | Draft 미재개, Approved 재개, Key Lock 시 차단, 완료 Item 재전송 0. Pairing/Relay는 R1-M6-04로 유보 |
| Conflict 3 Resolution | PASS | 명시 Conflict `1/1`, Resolution Subtest `3/3`, 서버 Sync Group `8/8` | `keep_local_as_new_version`, `keep_cloud`, `keep_both` 모두 명시 선택. `keep_cloud` 전송 0, 자동 덮어쓰기 0 |
| 원본 영역·Version 불변 | PASS | Domain `source_mutations/overwrite_count` Assertion, PostgreSQL `1/1`, Canon `4/4` | `source_mutations=0`, `overwrite_count=0`; 대상은 새 Object/Version으로 기록 |
| 무승인 Network·전송 0 | PASS | Domain 무승인 전송 `1/1`, Local Offline Network `1/1` | 승인 전·Scope 확대 거부 후 Transmission `0`; Offline Socket 연결 시도 `0`. Browser Network는 Browser 코드 미변경으로 미수행 |
| Audit·Trace | 검증 범위 PASS | 서버 API Full `126/126`의 M4 Audit 회귀, Runtime HTTP `1/1`, PostgreSQL Sync `1/1` | 기존 Audit 경계 유지 및 Trace 연결 Sync Row 영속화. 별도 Sync Audit Row Count는 측정하지 않았고 수치를 주장하지 않음 |

## 검증 결과

### 정적·로컬 Windows

- API 전체: `126 PASS / 22 external-platform SKIP`
- Local-service 전체: `90 PASS / 1 platform SKIP`
- Workspace Lint: `16 files PASS`
- OpenAPI: `52 paths`, `76 operations`, `69 schemas`, `28 errors`, SHA-256 `F7CE2D661F34A63080ADDBE4E0194898D5253D1E37030836E75535E9D6BD7E14`
- Repository Independence: `8 components`, `10 edges`, `10 package files`, 위반 `0`

### ysna-server PostgreSQL·API

- 환경: PostgreSQL `18.4`, pgvector `0.8.2`, Node `24.18.0`, npm `11.12.1`, uv `0.11.2`, Python `3.14.3`
- Migration `0001→0004 PASS`, 실제 `daon_app` 최소권한 PASS
- Sync PostgreSQL `1/1`, Sync Contract/Domain/Runtime/HTTP `8/8`
- Cloud/RLS `11/11`, Object Queue `16/16`, Canon `4/4`
- 실제 API `python -m daon_user_api.main`: `RUNTIME_READY=200`, `SYNC_PATHS=5`, 정상 종료
- 최종 API Full `126/126 PASS`
- 최종 Independence: `8 components`, `10 edges`, `10 package files`, `205 scanned files`, `0 violations`

### Linux Local 암호화 저장소

- 최종 `89 PASS / 2 platform SKIP`
- 암호화 Queue·Restart·Offline Network `0`·Key Lock 차단을 확인했다.

## 오류와 복구

- 첫 Restore는 Test 실행 뒤 생성한 Dump에 Fixture가 포함되어 Cloud Constraint Test 5건이 실패했다. Clean DB 재생성 → Migration → Clean Post-0004 Backup → Restore/Restart 후 Sync `1/1`, Cloud `11/11`, Object `16/16`, Canon `4/4`가 다시 통과했다. 제품 결함이 아니다.
- Linux Local 첫 실행은 Readonly Checkout에서 `TEMP=.`를 사용해 1건이 exit `67`로 실패했다. 격리된 쓰기 가능 임시 경로로 재실행해 통과했다.
- Python-only Runner의 Node 부재, Node+uv Runner의 Next CLI 부재는 Wrapper 환경 문제였다. 요구 Toolchain과 임시 Workspace `npm ci`로 API 전체를 통과했다.
- tmpfs Native TypeScript 실행 `EACCES`와 Node 22 Engine 불일치는 요구 버전을 낮추지 않고 Node `24.18.0`/npm `11.12.1` 일반 Ephemeral Rootfs로 재실행해 해결했다.

## 보호 경계·보존 자원

- `shared-db`, `netdata`, `nginx-proxy-manager` Container ID·이름 Before/After 불변
- `proxy-network` 불변, `common_default` Before/After 모두 없음
- Validation Container 잔존 `0`
- `PROTECTED_RESOURCES_UNCHANGED=true`, `SERVER_ALL_GATES_OK`

삭제 승인이 없어 Compose Project `daon_r1_m5_05_c01_365015a`의 Database Healthy, Object Storage Up, Network 1, DB/Object Volume 2, Checkout·Runtime을 보존했다. Cleanup은 신산님의 별도 승인 대상이다.

## 미수행·별도 Gate

- Browser 코드 미변경으로 Browser/Network 탭 검증은 수행하지 않았다.
- Release Test Plan TP Wave·Go/No-Go는 별도다.
- OCI 운영 배포와 격리 자원 Cleanup은 수행하지 않았다.

## 조치

구현과 필수 운영 증거 및 C02 산출물 보완은 완료됐다. 다음 판단은 격리 자원 Cleanup 승인 여부와 Release Test Plan TP Wave 진입 여부다.

`COMPLETED | R1-M5-05-C02 | 완료보고 정본 경로와 Manifest 필수 연결 보완 | 정본 완료보고·Evidence Index·구조화 Manifest·Progress | 기존 로컬·서버 Gate 유지, 문서 Parse/Diff 검증, Formal Failure 0 | Browser·TP Wave·OCI·Cleanup 미수행 | Cleanup과 TP Wave 진입은 신산님 별도 판단`

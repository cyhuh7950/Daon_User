# Object Storage·Transactional Outbox·Durable Worker 기반선

## 결정

Release 1 개발·통합 Object Adapter는 MinIO Python Client `7.2.20`과 MinIO Server `RELEASE.2025-09-07T16-13-09Z`를 사용한다. Client는 Python `>=3.9`·Apache-2.0이고, Server는 AGPL-3.0이다. Server는 제품 코드에 결합·재배포하지 않고 승인된 R1-D005 개발·격리 통합 환경의 외부 Service로만 운용한다. Server Image는 ARM64를 포함한 Manifest `sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`로 고정한다. 운영 Adapter 대상은 R1-D005의 OCI Object Storage이며 이번 작업은 운영 배포를 수행하지 않는다.

Durable Queue는 별도 In-memory Broker가 아니라 PostgreSQL Transactional Outbox와 Lease Job으로 구현한다. Object 등록 의도, Outbox Event, Job과 요청 Audit가 한 Transaction으로 Commit되며 Worker는 Workspace별 서버 검증 Scope 안에서 `FOR UPDATE SKIP LOCKED`로 Claim한다.

## Object 경계

- Client 파일명·경로·Prefix는 Object Key에 사용하지 않는다. Server는 ASCII Tenant·Workspace·`source|output` Prefix와 128-bit 불투명 ID만 조합한다.
- Binary는 `_staging` Prefix에 Digest·Size·검증 Content Type Metadata와 함께 먼저 저장한다. Domain Transaction이 실패한 Staging Object와 완료 후 Staging 사본은 `cleanup_pending` Reference와 Bucket Lifecycle로 M5-06이 정리한다.
- Worker는 Staging Object를 불변 Final Key로 멱등 Copy하고 최종 Metadata·Byte Size·SHA-256을 다시 검증한 뒤에만 Object Record·Job·Outbox·Audit를 완료한다.
- Worker Crash가 Copy 후 DB Ack 전에 발생해도 Lease 만료 후 같은 Final Key로 재실행하고 검증하므로 중복 성공을 만들지 않는다.
- Get은 현재 Service Authorization과 RLS가 반환한 Object Record의 Key만 Adapter에 전달한다. 내부 Endpoint·Bucket·Credential·Prefix는 공개 응답에 포함하지 않는다.
- Delete·Retention·Legal Hold와 Staging 정식 정리는 R1-M5-06이 소유한다.

## Queue·Worker 경계

- 상태는 `pending | leased | retry_wait | completed | dead_letter`로 고정한다.
- Payload Allowlist는 `object.promote` Schema v1의 `object_id` Reference 하나뿐이다. Secret·Token·Binary·개인정보를 넣지 않는다.
- Lease는 Owner·Until·Version을 모두 확인한다. 만료되거나 새 Worker가 회수한 Lease의 이전 Worker는 Ack·Fail을 Commit할 수 없다.
- Retry는 오류의 Retryable 분류와 bounded exponential backoff+jitter를 사용한다. 최대 Attempt 이후 Dead-letter로 전환한다.
- 재처리는 `queue.reprocess` Capability가 기존 Dead-letter를 변경하지 않고 새 Outbox·Job을 만드는 방식만 허용한다.
- Worker Shutdown은 새 Claim을 중단하고 현재 호출을 제한 시간 안에 종료한다. 시간 초과 Lease는 이후 만료 회수한다.
- 내부 Health/Metric은 Object 준비 상태, Pending·Retry·Lease·Dead-letter, 가장 오래된 대기 시간을 제공한다. 공개 Admin API와 전체 운영 화면은 이번 범위에 추가하지 않는다.

## Secret·배포

Runtime은 Object Endpoint·Bucket과 Access/Secret Key 파일 경로만 설정으로 받는다. Credential 값은 Secret 파일에서 Server-side로 해석하며 응답·Log·Evidence에 기록하지 않는다. Object 장애는 API Liveness와 분리하고 Readiness만 안전한 `not_ready`로 축소하며 같은 Process가 Object 복구 후 회복한다.

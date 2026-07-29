# R1-M4-07-C01 TDD Evidence

## RED

- Inbox 생성자에 승인된 필수 `recipient_id`가 없어 신규 격리 테스트 2건이 `TypeError`로 실패했다.
- Barrier 4개 Thread가 기존 분리 Lock의 Idempotency 조회와 Notification 조회를 동시에 통과하자 동일 Key 경쟁은 중복 Audit ID 오류를 냈고, 서로 다른 Key·동일 ETag 경쟁은 4건 모두 성공했다.
- 기존 테스트 5건은 PASS하여 결함이 C01 Recipient·동시성 경계에 한정됨을 확인했다.

## GREEN

- Inbox Tenant+Recipient 후보 제한, `REVIEW`·`APPROVE`·`DELIVER` Action 재인가, 권한 축소 후 Projection 제외를 검증했다.
- 동일 Key 4 Thread는 Version 2·동일 read_at을 재생하고 상태 전이·`notification.read` Audit 1건만 생성했다.
- 서로 다른 Key 4 Thread의 동일 stale ETag 경쟁은 성공 1·`VERSION_CONFLICT` 3건, Audit 1건이었다.
- 강제 Audit 실패는 Notification Version 1·unread와 미사용 Idempotency Key를 보존했고 같은 Key 재시도가 Version 2로 성공했다.
- 실제 Uvicorn Process HTTP 8요청도 동일 Key 200 4건 동일 결과, 서로 다른 Key 200 1건·412 3건, 읽음 Audit 총 2건을 확인했다.

## 경계

- 공개 Path·OpenAPI Schema·UI·Dependency·Lockfile·DB Migration은 변경하지 않았다.
- Reference Adapter는 Process-local 단일 Lock이며, M5는 동일 Port 의미를 PostgreSQL Transaction·Unique Key·Transactional Outbox로 구현해야 한다.

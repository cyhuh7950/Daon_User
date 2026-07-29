# R1-M4-07-C01 완료보고

## 판정

**COMPLETED — Inbox 개인 Recipient·요청별 Action 격리와 읽음 상태·Idempotency·Audit 최초 생성 결정의 단일 원자 경계를 구현했다.**

## 판단 이유

- Inbox는 동일 Tenant와 개인 Recipient가 모두 일치한 요청만 후보로 삼고 Review·Approval·Delivery별 현재 Action 권한을 다시 검사한다.
- Viewer·다른 Recipient·권한 축소 사용자는 대상 요청의 존재를 Projection으로 받지 않는다.
- 읽음 Repository 연산은 동일 Key 재생과 Fingerprint 충돌, Notification·ETag·unread 확인, Version·read_at·Idempotency 저장을 단일 Lock에서 처리한다.
- 최초 전이 Audit는 상태 Commit 전에 실행되어 Audit 실패가 읽음 상태만 남기지 않는다.
- Barrier Thread와 실제 Uvicorn HTTP 경쟁 모두 same-key 정확히 한 번과 different-key single winner를 입증했다.
- 공개 API Path·Schema·UI·Dependency·Lockfile와 승인된 권한 Matrix 의미는 변경하지 않았다.

## 조치

- C01 작업지시서·Prompt, 구현·테스트·Architecture·Evidence·진행기록을 단일 보완 Commit으로 Push한다.
- Chrome·ysna-server 재배포는 공개 API·UI 무변경이므로 R1-M4-07 기존 증거를 유지하고, C01은 실제 Uvicorn HTTP concurrent probe와 전체 Quality 회귀로 검증한다.
- M5 PostgreSQL Adapter는 이 Port 의미를 DB Transaction·Unique Idempotency Key·Transactional Outbox로 승계해야 한다.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| Notification Python | 10 PASS |
| API 전체 회귀 | 79 PASS · Windows POSIX-only 4 SKIP |
| BFF·OpenAPI·UI Node | 21 PASS |
| 실제 HTTP concurrency | 동일 Key 200×4·Version 2 동일·read_at 1개, 다른 Key 200×1·412×3, 읽음 Audit 2 |
| DB Migration | NOT_APPLICABLE |
| 공개 API·UI | 변경 0 |
| 전체 Quality Gate | 302.9초 · 7 Category PASS · failure 0 |

## Evidence

- `docs/03_evidence/release_1/R1-M4-07-C01/tdd-evidence.md`
- `docs/03_evidence/release_1/R1-M4-07-C01/http-concurrency-summary.json`
- `docs/03_evidence/release_1/R1-M4-07-C01/validation-summary.json`
- `docs/04_test_reports/release_1/R1-M4-07-C01_progress.md`

## 제외 범위·남은 위험

- Process-local Adapter의 재시작 지속성과 다중 Replica 원자성은 주장하지 않는다.
- PostgreSQL Migration·Outbox·Worker, Push·Email, M8 업무 Write는 후속 범위다.
- PR·CI·Merge와 최종 완료 판정은 어울1 소유다.

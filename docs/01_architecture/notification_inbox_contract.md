# Notification·Inbox 실행 계약

## 적용 범위

R1-M4-07은 정책·권한·실행 상태 Event를 현재 권한이 있는 Recipient의 In-app Notification으로 연결하고, Review·Approval·Delivery 원 요청을 읽기 전용 Inbox Projection으로 제공한다. 실제 Push·Email, PostgreSQL Migration·Outbox·Worker와 M8 업무 Write는 후속 범위다.

## 공개 API

| Method | Path | 의미 |
| --- | --- | --- |
| GET | `/api/v1/notifications` | 현재 Tenant·Recipient·권한으로 재검증한 Cursor 목록과 미읽음 수 |
| GET | `/api/v1/notifications/{id}` | 현재 권한으로 재검증한 단건과 허용 Deep Link |
| PATCH | `/api/v1/notifications/{id}` | `If-Match`·`Idempotency-Key`가 있는 `unread → read` 단방향 전이 |
| GET | `/api/v1/inbox` | 원 Review·Approval·Delivery 요청의 현재 읽기 Projection |

목록 Query는 `cursor`, `limit`, `filter`, `search`만 허용한다. Notification filter는 state·kind·severity, Inbox filter는 kind·state Allowlist만 사용한다. 미정의 Query·Body, 변조 Cursor, 과대 Limit와 안전하지 않은 검색값은 400으로 fail-close한다.

## 생성·권한·중복 억제

1. 신뢰된 Server Producer가 Event와 Recipient 후보 집합을 전달한다. 공개 Create API는 없다.
2. Notification Service는 각 후보의 Tenant 일치와 현재 `AuthorizationService`의 Workspace `view` 결정을 다시 계산한다. Client가 보낸 Recipient·Role·Grant는 입력 계약에 존재하지 않는다.
3. `source_event_id + recipient_id + kind`의 안정 Key로 중복 Event를 억제한다. 같은 Event 재전달은 Notification과 생성 Audit를 추가하지 않는다.
4. 목록·단건·읽음·Inbox마다 현재 권한을 재검증한다. Tenant·Recipient가 다르면 비노출하고 권한이 축소되면 Notification은 `CURRENT_ACCESS_DENIED`, Inbox는 해당 Projection 제외로 차단한다.
5. 생성과 읽음 전이는 같은 Trace 계보의 append-only Audit를 남긴다.

Inbox 원 요청은 신뢰된 Server Producer가 확정한 필수 `recipient_id`를 가진다. 저장 후보 중 Tenant와 개인 Recipient가 모두 현재 Principal과 일치한 항목만 평가하며, 요청 종류마다 `review → REVIEW`, `approval → APPROVE`, `delivery → DELIVER` 현재 Action 권한을 다시 검사한다. 공개 응답과 Create API에는 Recipient 지정 기능을 노출하지 않는다. M8의 역할·그룹 할당은 권한 있는 개인 Recipient별 Projection으로 확장한다.

## 읽음 전이와 동시성

- 생성 Version은 1이며 응답 ETag는 `"notification-{version}"`이다.
- 읽음 요청은 정확한 ETag와 16자 이상 Idempotency Key를 요구한다.
- 동일 Key·동일 요청 재실행은 최초 응답을 재생한다. 동일 Key의 다른 요청은 409, stale ETag는 412다.
- `read → unread`와 이미 읽은 항목의 새 Key 재전이는 허용하지 않는다.
- Repository Port의 `transition_read`는 Idempotency 조회·Fingerprint 충돌·현재 Notification/ETag/unread 확인·Version/read_at 변경·Idempotency 결과 저장·최초 전이 결정을 단일 Lock/Transaction에서 수행한다.
- Reference Adapter는 최초 전이 Audit callback을 상태 Commit 전에 수행하여 Audit 실패가 읽음 상태만 남기지 않게 한다. M5 PostgreSQL Adapter는 같은 원자 경계를 DB Transaction과 Transactional Outbox·Unique Key로 구현해야 한다.

## Deep Link와 표시 안전성

- Web은 `/operations`, `/inbox`, `/notifications`, `/workspaces` 아래 same-origin 상대 경로만 허용한다.
- Native는 `sinsan-daon://app/{allowlisted-route}`만 허용한다.
- 외부 URL, protocol-relative URL, 미승인 Native route, query·fragment가 붙은 Native route는 거부한다.
- 제목·요약은 길이가 제한된 Plain text다. `<`, `>`, 제어문자를 저장하지 않고 UI는 `dangerouslySetInnerHTML`을 사용하지 않는다.
- 오류 응답과 Evidence에는 Credential, DB 경로, 내부 Host, Stack을 포함하지 않는다.

## Client와 BFF

Web UI는 공용 `NotificationInboxWorkspace`에 네트워크 동작이 없는 상태·표시 계약만 둔다. Web 전용 `notification-inbox-api.js`가 same-origin `/api/v1/...` 상대 경로를 호출하고, Next catch-all Route와 고정 BFF Allowlist가 Cookie·CSRF·Header·Query·Body 상한을 적용한다. Browser Source에는 내부 API Origin이나 `NEXT_PUBLIC_API_BASE_URL`을 두지 않는다.

## 저장 경계와 후속 위험

- M4 Reference Adapter는 실제 Process에서 동작하지만 Process-local 비영속이다. 재시작 후 Notification·읽음·Cursor가 유지된다고 주장하지 않는다.
- M5는 Repository Port를 PostgreSQL·Outbox·Worker로 교체하고 안정 Cursor·멱등성·Deduplication의 지속성을 보존해야 한다.
- Process-local Lock은 단일 Process 동시성만 보장한다. 다중 Replica의 정확히 한 번 상태·Audit 결정은 M5 DB Transaction·Unique Idempotency Key·Transactional Outbox의 필수 인수조건이다.
- Inbox는 원 Domain 상태의 Projection이며 별도 승인·반려·전달 Write API를 만들지 않는다.
- 기존 공통 Rate-limit 공개 계약은 없다. 이번 작업에서 임의 설정값을 추가하지 않았고 M5 Gateway 운영 정책에서 별도로 확정해야 한다.

## 검증 기준

- Domain/API: Event dedupe, 현재 ACL, Tenant·Recipient 비노출, Cursor, ETag·Idempotency, Audit·Trace, Inbox Projection.
- BFF/OpenAPI: 고정 Path·Method·Query·Header, same-origin CSRF, 안전 오류, 47 Path·71 Operation·61 Schema.
- Browser: 실제 API·Next Production Process에서 목록→읽음→새로고침 유지, Inbox→허용 Deep Link.
- 운영 경계: DB Migration `NOT_APPLICABLE`, 검증 종료 후 Browser·API·Next Process와 Listener 0건.

## C01 독립검토 보완

어울1의 독립 보안·Diff 검토에서 발견된 Recipient 미격리와 분리 Lock 경쟁은 `R1-M4-07-C01`에서 보완했다. 공개 Path·Schema·UI는 변경하지 않았으며, Barrier Thread 검증과 실제 Uvicorn HTTP 8요청 경쟁 증거는 `docs/03_evidence/release_1/R1-M4-07-C01/`을 정본으로 한다.

# R1-M4-07-C01 Inbox Recipient·Action 격리와 읽음 원자성 중대 보완 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-07-C01`.
- Branch `codex/r1-m4-07`, 기준 HEAD `5759f02cdfdf6f113de59b811cb2dbf5850e01cd`, 시작 Clean.
- 어울1 독립 Diff·보안 검토에서 발견한 Inbox 대상 격리와 concurrent idempotency 결함을 적용한다.
- 어울2가 이 Worktree와 범위의 유일한 Writer다. PR·CI·Merge와 완료 판정은 어울1 소유다.

## 판정과 단일 목표

- 판정: `MAJOR_GAP / CORRECTION_REQUIRED`.
- `InboxRequest`에는 Recipient가 없고 `list_inbox`는 Tenant·Workspace `Action.VIEW`만 검사한다. 따라서 같은 Workspace의 Viewer가 자신에게 할당되지 않은 Review·Approval·Delivery 요청을 조회할 수 있어 R1-D024의 권한 있는 Recipient Projection 계약을 위반한다.
- `mark_read`는 idempotency 조회, Notification 조회·교체, idempotency 결과 저장이 서로 다른 Lock 구간이다. 동일 ETag·Key의 동시 요청이 둘 다 unread를 읽어 Version 전이와 Audit를 중복 생성할 수 있어 정확히 한 번 계약을 보장하지 못한다.
- 목표: Inbox를 지정 Recipient와 요청 종류별 현재 Action 권한으로 격리하고, 읽음 상태·Idempotency 결과·Audit 생성 결정을 원자적 Repository 계약으로 만들어 동시 요청에서도 상태 전이와 성공 Audit가 정확히 한 번만 발생하게 한다.

## Inbox Recipient·Action 계약

- `InboxRequest`에 신뢰된 Server Producer가 확정한 `recipient_id`를 필수로 추가한다. R1-M4 Reference Adapter는 개인 Recipient Projection만 저장한다. M8의 역할·그룹 할당 Producer는 권한 있는 개인 Recipient별 Projection으로 확장한다.
- `project_request`는 Recipient ID를 동일 ID Allowlist로 검증한다. Client가 Recipient·Role·Grant를 지정하는 공개 Create API는 계속 0건이다.
- `list_inbox`는 `tenant_id == principal.tenant_id`와 `recipient_id == principal.user_id`를 모두 만족한 항목만 후보로 삼는다. 다른 Recipient·Tenant 항목은 존재를 노출하지 않는다.
- 후보마다 Workspace 현재 권한과 요청 종류별 Action을 다시 검사한다: `review → Action.REVIEW`, `approval → Action.APPROVE`, `delivery → Action.DELIVER`. Viewer 또는 권한 축소 사용자는 Projection을 받지 못한다.
- `inbox_json`은 자신의 응답에 Recipient ID를 노출할 필요가 없다. 현재 안전 응답 필드는 유지한다.

## 읽음·Idempotency 원자성 계약

- Repository Port에 읽음 전이를 원자적으로 수행하는 명시 연산을 둔다. 하나의 Lock/Transaction 안에서 `idempotency key 조회 → fingerprint 충돌 확인 → 현재 Notification·ETag·unread 확인 → version/read_at 변경 → idempotency 결과 저장 → 최초 전이 여부 반환`을 완료한다.
- 동일 Key·동일 요청 동시 실행은 모두 같은 Version·응답을 얻고 실제 상태 전이와 성공 Audit 생성 결정은 1회다. 같은 Key·다른 fingerprint는 409, 서로 다른 Key·같은 stale ETag 경쟁은 하나만 성공하고 나머지는 412 또는 승인된 안정 충돌이다.
- Audit는 Repository 원자 연산이 `first_transition=true`를 반환한 요청만 1회 생성한다. Reference Adapter의 Process 동시성뿐 아니라 M5 PostgreSQL Adapter가 같은 Transaction/Unique Key 계약을 구현할 수 있도록 Port 의미를 문서화한다.
- 상태를 먼저 바꾼 뒤 Audit 생성이 실패하는 경계는 Reference Adapter의 제한으로 숨기지 않는다. 현재 Audit draft 검증을 전이 전에 완료하거나 동등하게 실패 가능성을 제거하고, M5 Transaction·Outbox 후속 책임을 계약에 기록한다.

## 허용·제외 범위

- 허용: Notification Domain·Repository Port/Reference Adapter, 직접 관련 Runtime fixture·tests, Architecture/Evidence/진행·완료보고, 기존 서버 검증 Evidence 연결.
- 제외: 공개 Path/Schema 추가, Push·Email, PostgreSQL Migration·Outbox·Worker 구현, UI 재설계, Dependency/Lockfile 변경, 권한 Matrix 의미 변경, Quality 기준 완화.
- R1-D024, same-origin BFF, OpenAPI 응답 의미, Browser 완료 증거와 기존 7개 Quality 범주를 보존한다.

## TDD·필수 검증

- RED: 같은 Tenant·Workspace의 `viewer` 또는 다른 `recipient_id` 사용자가 Approval/Review/Delivery InboxItem을 조회하지 못해야 하는 테스트를 먼저 추가하고 기존 구현 실패를 증명한다.
- RED: Barrier/Thread fixture로 동일 Key 동시 2개 이상과 서로 다른 Key·동일 ETag 경쟁을 실행해 기존 분리 Lock 계약의 중복 가능성을 재현한다. 시간 의존 sleep만으로 경쟁을 가정하지 않는다.
- GREEN: Recipient 일치 + Review/Approve/Deliver Action 각각의 허용/거부 Matrix, 권한 축소, Tenant/Recipient 비노출, Deep Link 현재 권한을 검증한다.
- GREEN: 동일 Key 동시 요청은 동일 결과·Version 증가 1·`notification.read` Audit 1, 다른 Key 경쟁은 성공 1·충돌 N-1, 상태·Idempotency 원장 불일치 0을 반복 검증한다.
- Notification Python, 전체 API, BFF/OpenAPI/UI, Identity·Authorization·Audit, actual process, Quality·Independence를 재실행한다. 공개 API·UI가 바뀌지 않으므로 Chrome·ysna 전체 재배포는 기존 증거와 계약 회귀로 대체할 수 있지만 실제 HTTP concurrent probe는 필수다.

## 진행·보고

- `docs/04_test_reports/release_1/R1-M4-07-C01_progress.md`에 착수, RED, 설계 선택, 구현, 동시성 검증, 회귀, 오류·복구, 종료 직전을 기록한다.
- 기존 R1-M4-07 완료보고·Architecture·Evidence에 C01 후속을 연결하고 표준 완료보고를 작성한다.
- 단일 보완 Commit을 Push하고 Local/Remote SHA·Clean·잔여 Process 0과 표준 상태를 보고한다.

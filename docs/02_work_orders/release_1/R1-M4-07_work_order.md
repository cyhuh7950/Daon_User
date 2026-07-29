# R1-M4-07 Notification·Inbox 기반 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-07`.
- Branch `codex/r1-m4-07`, 기준 HEAD `6577426d78a10ec5dfd1011095bc528745c6381e`, 시작 Clean.
- 승인 정본: `AGENTS.md`, 상세 설계서 §14.5·§16·§17·§21, Release 1 작업계획 §14의 R1-M4-07, 테스트계획과 시나리오 04·05·06, 결정 `R1-D024`·승인 `APR-R1-M4-07-NOTIFICATION-API-20260729-01`.
- 선행 R1-M2-07 UI·Reducer와 R1-M4-02 Audit, R1-M4-03 Identity, R1-M4-04 Authorization, R1-M4-05 BFF·Gateway를 재사용하되 Prototype Fixture를 실제 Backend 성공으로 승격하지 않는다.
- 어울2가 이 Worktree와 범위의 유일한 Writer다. PR·CI·Merge와 완료 판정은 어울1 소유다.

## 단일 목표와 사용자 완료 조건

- 목표: 권한 있는 사용자가 Web·Native 공통 의미로 In-app 알림과 실행 가능한 Inbox를 조회하고, 알림을 안전하게 읽음 처리하며 원 Event·Resource·Audit·Trace로 이동할 수 있는 실제 API·BFF 기반을 완성한다.
- 사용자는 자신의 현재 권한 범위 알림만 Cursor 목록으로 보고 미읽음 수·전달 상태를 확인한다. 읽음 처리는 새로고침·재요청 후에도 동일 의미를 유지한다.
- 사용자는 Inbox에서 Review·Approval·Delivery 등 원 요청의 현재 상태와 허용 Deep Link를 확인한다. Inbox 자체에서 소유 Domain의 승인·반려·전달 Write를 우회하지 않는다.
- 정책·권한·실행 상태 Event가 허용 Recipient의 Notification과 Audit에 정확히 한 번 연결되고, 권한 축소·Tenant 교차·위조 대상·안전하지 않은 Deep Link는 노출되지 않는다.

## 승인된 공개 API 계약

- `GET /api/v1/notifications`: Cursor·Limit·Filter·Search, 현재 Recipient 필터, `unread_count`, 안정 정렬을 제공한다.
- `GET /api/v1/notifications/{id}`: 단건을 현재 AccessDecision으로 재검증하고 허용된 same-origin 또는 승인 Native Route Deep Link만 반환한다.
- `PATCH /api/v1/notifications/{id}`: `If-Match`와 `Idempotency-Key` 필수, `unread → read` 단방향 전이만 허용한다. 동일 Key 재요청은 동일 응답이며 Version 불일치 412, Key 재사용 충돌 409다.
- `GET /api/v1/inbox`: ReviewRequest·ApprovalRequest·Delivery 등 원 요청의 읽기 Projection만 반환하며 별도 Inbox Write를 만들지 않는다.
- Web Browser는 `/api/v1/...` same-origin BFF만 호출한다. Native는 승인 HTTPS Gateway와 같은 OpenAPI 의미를 사용한다.

## Notification·Inbox 데이터와 이벤트 계약

- Notification은 불변 ID, Tenant·Workspace·Recipient, Kind·Severity, 안전 제목·요약, Source Event ID·종류, Resource Type·ID, 허용 Deep Link, AuditEvent ID·Trace ID, `pending | delivered | failed | suppressed`, created/delivered/read 시각과 Version·ETag를 가진다.
- Source Event·Recipient·Kind 조합의 안정 Deduplication Key로 중복 생성을 억제한다. Event 재전달은 Notification 0개 추가·Audit 성공 중복 0건이어야 한다.
- 초기 실제 Event 연결은 선행 기능으로 검증 가능한 인증/장치, Membership·권한·정책, Run 상태·실패 경고를 포함한다. Review·Approval·Delivery 생산자는 M8이 같은 계약에 연결한다.
- Recipient는 Server가 현재 Membership·Capability·Resource ACL로 계산한다. Client가 보낸 Recipient·Role·Grant·Deep Link·전달 성공 주장을 신뢰하지 않는다.
- 목록·단건·읽음·Deep Link마다 현재 권한을 재검증한다. 보존된 Notification은 과거 권한을 부여하지 않으며 권한 축소 시 대상 내용은 마스킹하거나 `CURRENT_ACCESS_DENIED`로 차단한다.
- 알림 본문·오류·Audit에 Token, Cookie, Provider 원문, DB/내부 Host, Stack, Secret 이름과 민감 Source 내용을 기록하지 않는다.
- ID·Cursor·Limit·Filter·Search·Header·Body는 기존 OpenAPI 길이·형식 상한과 명시 Allowlist로 검증한다. 제목·요약은 Plain text로만 취급하고 사용자 HTML을 생성·저장·렌더링하지 않는다.
- InboxItem은 원 요청 ID·종류·상태·Actor·기한·Resource·허용 Deep Link를 조합한 Projection이며 원 요청이 취소·만료·권한 축소되면 즉시 현재 상태를 반영한다.

## 저장·전송·후속 경계

- M4에서는 실제 API 실행이 가능한 Repository Port와 격리 Reference Adapter를 구현한다. PostgreSQL Migration·Outbox·Durable Worker는 M5 소유이며 이번 작업에서 임시 운영 DB 구조를 만들지 않는다.
- 실제 채널은 In-app만 `delivered`로 기록한다. Push·Email·OS Remote Notification은 계정·Credential·후속 Adapter 없이 성공으로 기록하지 않고 `suppressed` 또는 미지원 상태를 안전하게 표현한다.
- M2-07 Notification UI의 Fixture·`deferred_actual` 문구를 실제 Adapter 상태와 혼합하지 않는다. Notifications·Inbox 화면은 실제 BFF 성공, loading·empty·forbidden·unavailable·safe error를 구분한다.
- 새 Dependency와 Lock 변경은 기존 표준 라이브러리·구조로 충족할 수 없다는 증거가 있을 때만 어울1 판단을 요청한다.

## 허용·제외 범위

- 허용: OpenAPI v1 Notification·Inbox Path/Schema, API Domain·Repository Port·HTTP Route, Web same-origin BFF와 Notifications·Inbox Adapter/UI, 직접 관련 계약·단위·통합·Browser 검사, R1-M4-07 Architecture/Evidence/진행·완료보고.
- 제외: Push·Email 실제 발송, APNs/FCM Credential, PostgreSQL Migration·Outbox·Queue Worker, M8 Review/Approval/Delivery 업무 Write, M9 운영 복구, Local Service 업무 확장, UI 전면 재설계, Dependency 임의 변경.
- 기존 Auth·Tenant·AccessDecision·Audit·Trace·safe error·Cookie/CSRF·Native Bearer와 BFF Graceful Shutdown 의미를 보존한다.

## TDD·필수 검증

- RED에서 Notification API 부재, Prototype 읽음 전이의 비영속·무권한 상태, 중복 Event·Tenant 교차·권한 축소·ETag/Idempotency·Deep Link 공격의 기존 차단 부재를 먼저 증명한다.
- Domain/contract: Event dedupe, Recipient 계산, 전달 상태, 읽음 전이, ETag·Idempotency, Cursor 안정성, Inbox Projection, 현재 ACL 재검증, Audit·Trace 연결을 검증한다.
- Negative: 다른 Tenant/Recipient 403/404 비노출, 권한 축소 마스킹/차단, 위조 Deep Link·Role·Grant 무시, 중복 Event 0개 추가, stale ETag 412, Key 충돌 409, Token/Cookie/Stack/내부주소 반사 0건을 검증한다.
- Security: 인증 없는 요청, CSRF 누락/위조, 과대·미정의 Query/Header/Body, HTML/Script 제목·요약, Cursor 변조와 허용되지 않은 Redirect/Deep Link를 fail-close하고 CSP·Cookie·CORS·기존 Gateway 요청 제한을 회귀 검증한다. 기존 Rate-limit 계약이 없다면 임의 설정값을 추가하지 말고 증거와 후속 위험을 보고한다.
- HTTP/BFF: Web Cookie+CSRF와 Native Bearer가 같은 응답 의미를 가지며 Browser Source에 절대 API 주소·localhost·Docker Host/Port·`NEXT_PUBLIC_API_BASE_URL` 직접 호출이 0건이어야 한다.
- 실제 Browser에서 Notifications 목록→읽음 처리→새로고침→상태 유지, Inbox→허용 Deep Link를 클릭하고 Network가 same-origin `/api/v1/...`인지 확인한다. GUI 사용 시 종료 후 Browser·개발 Process를 모두 닫고 신산님의 화면을 점유하지 않는다.
- API 단위·통합·OpenAPI contract/diff, BFF contract/runtime, Identity·Authorization·Audit 회귀, Web lint/build, Quality·Independence를 실행한다.
- 로컬 검증 후 Commit·Push하고 `/home/ubuntu/deploy/daon-user` 격리 배포에서 정확 Commit SHA·전용 Compose 경계·Health·실제 HTTP/BFF를 확인한다. M4-07 DB Migration은 범위 제외이므로 `NOT_APPLICABLE` 근거를 남기며 기존 DB·Volume을 변경하지 않는다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M4-07_progress.md`에 착수, RED, 각 구현 단계, 오류·복구, 로컬 검증, Push, ysna-server 배포·서버 검증, 종료 직전을 시각·상태·변경 파일·명령/결과·원인/복구·다음 작업과 함께 기록한다.
- Evidence는 `docs/03_evidence/release_1/R1-M4-07/`에 실제·정적·Mock을 구분하고 Secret 원문 없이 저장한다.
- 완료보고는 `판정 → 판단 이유 → 조치` 순서와 표준 상태 계약으로 작성한다. 단일 구현 Commit을 Push하고 Local/Remote SHA·Clean을 보고한다.
- 실제 코드가 승인 계약과 충돌하거나 공개 API·데이터·보안 경계를 추가 변경해야 하면 구현을 멈추고 증거와 함께 어울1에게 반환한다.

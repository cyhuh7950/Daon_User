# R1-M4-07 완료보고

## 판정

**COMPLETED — Notification·Inbox 실제 API·same-origin BFF·Web UI와 현재 권한·중복 억제·읽음 ETag/멱등성·안전 Deep Link 계약을 구현하고 실제 Process·Browser 및 전체 Quality Gate를 통과했다.**

## 판단 이유

- Event·Recipient·Kind 조합은 정확히 한 번만 Notification과 생성 Audit를 만든다.
- 목록·단건·읽음·Inbox가 현재 Identity·Tenant·Recipient·Workspace 권한을 재검증하며 교차 Tenant·Recipient와 권한 축소를 비노출 또는 차단한다.
- 읽음 처리는 ETag와 Idempotency Key를 요구하고 stale Version·Key 충돌·역방향 상태 전이를 거부한다.
- Inbox는 원 Approval·Review·Delivery 요청의 읽기 Projection이며 업무 Write 우회 Route를 만들지 않았다.
- Web은 공용 UI와 Web 전용 same-origin Adapter를 분리했고 Browser Source의 절대 API 주소·localhost·Docker Host·`NEXT_PUBLIC_API_BASE_URL` 사용은 0건이다.
- 실제 API·Next Production Process와 Chrome에서 알림 목록 미읽음 1→읽음→미읽음 0→새로고침 유지, Inbox pending 1건→허용 Deep Link 이동을 확인했다.
- 검증 후 자동 Chrome Tab, API·Next Process와 Listener가 모두 0건이다.
- 최종 Quality Gate는 7개 범주 전부 PASS, failure 0이다.

## 조치

- Branch `codex/r1-m4-07`의 단일 구현 Commit `4fa59c7e4c43948442307205572fd181a6f19d54`을 Push하고 ysna-server 격리 배포에서 exact SHA·전용 Compose 경계·Health·실제 HTTP/BFF를 검증했다.
- DB Migration은 M5 소유이므로 `NOT_APPLICABLE`로 기록하고 기존 DB·Volume을 변경하지 않는다.
- PR·CI·Merge와 다음 Work Order 착수 판단은 어울1이 수행한다.

## 주요 변경

- `notification.py`: Domain, Repository Port, process-local Reference Adapter, Event dedupe, 현재 ACL, Cursor, ETag·Idempotency, Audit·Trace, Inbox Projection.
- `runtime.py`: Notification 목록·단건·읽음과 Inbox Route, strict Query·Body·Header와 안전 오류.
- OpenAPI v1: 3 Path·4 Operation 및 Notification·Inbox Schema/Response.
- Next BFF: `/api/v1/...` catch-all, 고정 Notification·Inbox Route·Method·Query·Header·CSRF 경계.
- Web UI: 실제 loading·empty·forbidden·unavailable·error·ready, 읽음 처리, 허용 Deep Link, 공용 UI와 Web API Adapter 분리.
- 검증: Python·Node TDD, OpenAPI 결정적 증거, 실제 Browser/Process fixture, Architecture·Evidence.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| Notification Python | 6 PASS |
| API 전체 회귀 | 75 PASS · Windows에서 POSIX-only 4 SKIP |
| BFF·OpenAPI·UI Node | 21 PASS |
| Workspace Lint | 16 files PASS |
| Web Production build | PASS · `/api/v1/[...path]`, `/notifications`, `/inbox` |
| OpenAPI | 47 Path · 71 Operation · 61 Schema PASS |
| 실제 API·Next Process | Notification 200 · Inbox 200 · 내부주소/자격증명 반사 0 |
| 실제 Chrome | 읽음·새로고침 유지·Inbox Deep Link PASS |
| 최종 Quality Gate | 7 Category PASS · failure 0 |
| ysna-server ARM64 | exact SHA · npm ci 508 · Python 6 · Node 21 · OpenAPI/Lint/Web Build PASS |
| ysna-server 실제 HTTP/BFF | Notification·Inbox 200 · 읽음 후 unread 0 · Runtime exit 0 |
| 서버 자원 보호 | Migration N/A · DB 명령 0 · 기존 Container/Network/Volume Hash 불변 · 임시 자원 0 |
| 종료 정리 | Browser Tab·owned Process·Listener 0 |

## Evidence

- `docs/03_evidence/release_1/R1-M4-07/tdd-evidence.md`
- `docs/03_evidence/release_1/R1-M4-07/contract-summary.json`
- `docs/03_evidence/release_1/R1-M4-07/runtime-process-summary.json`
- `docs/03_evidence/release_1/R1-M4-07/browser-e2e-summary.json`
- `docs/03_evidence/release_1/R1-M4-07/validation-summary.json`
- `docs/03_evidence/release_1/R1-M4-07/server-validation-manifest.json`
- `docs/03_evidence/release_1/R1-M4-07/server-validation-summary.md`
- `docs/04_test_reports/release_1/R1-M4-07_progress.md`

## 제외 범위·남은 위험

- M4 Reference Adapter는 실제 실행되지만 Process-local 비영속이다. PostgreSQL·Outbox·Worker·durable Cursor와 재시작 후 읽음 지속성은 M5 소유다.
- Push·Email·APNs·FCM 실제 전달은 수행하거나 성공으로 주장하지 않았다.
- 공통 Rate-limit 공개 계약이 없어 임의 설정값을 추가하지 않았다. M5 Gateway 운영 정책에서 확정해야 한다.
- 최종 GitHub CI와 Merge 판정은 어울1 소유다.

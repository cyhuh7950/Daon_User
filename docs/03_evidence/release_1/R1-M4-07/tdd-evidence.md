# R1-M4-07 TDD Evidence

## RED

- Python import: `ModuleNotFoundError: daon_user_api.notification`.
- 신규 BFF Notification·Inbox Path: 404.
- OpenAPI Notification·Inbox Path: 부재.
- 기존 Node 회귀 17건은 PASS하여 실패가 신규 기능 부재에 한정됨을 확인했다.

## GREEN

- Notification Python Domain·HTTP 6건 PASS.
- BFF·OpenAPI·UI Node 21건 PASS.
- Web Workspace Lint 16개 파일 PASS, Web Production build PASS.
- OpenAPI 결정적 증거: 47 Path·71 Operation·61 Schema, SHA-256 `37E0A4ABAA00D1CEE9C6E3A0D3B0173233D637705D434642921CE8124A88C162`.
- 실제 API·Next Process와 Chrome에서 읽음 전이·새로고침 유지·Inbox Deep Link PASS.

## 보완과 회귀

- 공용 UI의 직접 `fetch`를 RED로 확인한 뒤 Web 전용 same-origin Adapter로 분리하고 정확한 Adapter 한 파일만 Lint 허용 경계에 등록했다.
- 1차 전체 Gate의 유일한 실패는 ASGI 단위 테스트의 loopback `base_url` 문자열이 전역 Browser 주소 Rule에 걸린 것이었다. Rule을 완화하지 않고 예약 `.invalid` Origin으로 교정했다.
- 최종 Quality Gate: lint·type·unit·contract·build·security·independence 전부 PASS, failure 0.

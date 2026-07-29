# R1-M4-07 ysna-server 검증 요약

- 판정: `PASS`
- 불변 구현 SHA: `4fa59c7e4c43948442307205572fd181a6f19d54`
- 격리 Checkout: `/home/ubuntu/deploy/daon-user/R1-M4-07/4fa59c7e4c43948442307205572fd181a6f19d54`, detached·사전/사후 Clean
- ARM64: Host·Docker·고정 Node/UV·검증 Image 모두 ARM64
- Toolchain: Node 24.18.0, npm 11.12.1, Corepack 0.35.0, UV 0.11.2, Python 3.14.3
- Compose 경계: 전용 Project·Network 1·Volume 10·Container 1, Checkout read-only bind. 검증 후 전부 제거되어 잔여 0
- 검증: npm ci 508 packages, Notification Python 6, BFF/OpenAPI/UI Node 21, OpenAPI 47/71/61, Lint 16 files, Web Production Build PASS
- 실제 HTTP/BFF: Notification 목록·단건·읽음 PATCH·새로고침과 Inbox 모두 200, 읽음 후 unread 0, 허용 same-origin Deep Link, 내부주소/자격증명 반사 0
- Migration: `NOT_APPLICABLE`, DB 명령 0, 기존 DB·Volume 변경 0
- 기존 자원 보호: Container·Network·Volume 사전/사후 Hash 각각 일치

원격 변수 인용, read-only bind mountpoint, Node Image의 bundled npm 버전 차이는 승인 경계를 바꾸지 않고 각각 원인을 확인해 복구했다. Checkout을 writable로 바꾸거나 Engine 정책을 완화하지 않았고, 임시 Image·Compose 파일·mountpoint와 전용 Compose 자원만 제거했다.

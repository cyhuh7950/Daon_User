# M4 공개 API·인증·권한 Exit 검증 보고서

## 1. 검증 정보

| 항목 | 값 |
| --- | --- |
| 검증자 | 어울1 · 설계·기술 책임자 |
| 검증일 | 2026-07-29 |
| 대상 기준선 | `ee71d83fea6aef2e53d23cae2f8863e0741cd659` |
| 대상 Work Order | `R1-M4-01`~`R1-M4-07`, 승인된 보완 작업 |
| 검증 성격 | Milestone Exit 1차 검증 |
| 독립 테스트 웨이브 | M6 Exit의 `TP-2`에서 M1·M4·M5·M6 계약을 함께 검증 |

## 2. 판정

**판정: PASS — M5 진입 가능.**

## 3. 판단 이유

- 공개 OpenAPI, Web same-origin BFF, Native Gateway의 응답 의미와 안전 오류 계약을 구현·검증했다.
- 인증·Session·PKCE·Device·Step-up, 현재 권한 기반 Tenant·Workspace·Resource 접근, Audit·Trace 계약을 구현·검증했다.
- Write의 `Idempotency-Key`, `If-Match` 기반 낙관적 동시성, 중복·경쟁 요청 결과를 자동 및 실제 HTTP로 확인했다.
- Notification·Inbox는 Recipient와 Action별 현재 권한을 재검증하며, 읽음 전이는 Reference Adapter 안에서 상태·멱등 결과·Audit을 원자적으로 처리한다.
- Browser Network는 same-origin `/api/v1/...`만 사용하며 Client 코드의 API 절대주소·`localhost`·Docker 내부 주소 직접 호출이 없음을 확인했다.
- ysna-server에서 정확 Commit SHA와 격리 경계를 사용해 ARM64 Build·API·BFF 통합 검증을 통과했고 기존 공용 자원을 변경하지 않았다.
- GitHub Quality와 iOS CI가 대상 SHA에서 통과했다. R1-M4-07 최종 Quality Run은 `30436043790`, iOS Run은 `30436046502`다.
- Stack Trace·DB Host·Provider 원문·Secret 이름의 외부 노출, Tenant 교차 접근, Step-up 우회, 권한 철회 후 비인가 원문 접근은 검증 범위에서 발견되지 않았다.

## 4. M5 이관 계약과 위험

- M4 Repository는 공개 계약을 검증하기 위한 격리 Reference Adapter다. PostgreSQL 영속화·RLS·Transaction·Outbox는 M5 소유이며 운영 정본으로 오인하지 않는다.
- R1-M5-01은 Identity·Authorization·Audit·Notification의 기존 의미를 PostgreSQL Transaction 경계로 승계하고, 특히 Notification 읽음 상태·Idempotency 결과·Audit의 원자성을 DB 경쟁 요청으로 다시 입증해야 한다.
- Push·Email 실제 발송, Queue·Worker·Object Storage는 각각 후속 Work Order 범위이며 M4의 In-app 성공과 혼동하지 않는다.
- 전체 보안 독립 검증은 `TP-5`, M4 계약의 독립 재검증은 M6 Exit `TP-2`에서 수행한다.

## 5. 조치

- `R1-M5-01` Cloud 정본·격리 작업을 시작한다.
- 정식 `FAILURE_REPORT` 누적은 0회로 유지한다.
- `TP-2` 도달 전까지 Milestone별 어울1 검증을 계속하고, 테스트계획 지정 시점에는 신산님에게 결과와 Go/No-Go를 보고한다.

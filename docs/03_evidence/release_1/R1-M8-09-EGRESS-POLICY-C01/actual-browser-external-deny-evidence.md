# Actual Browser External Question Deny Gate

- 결과: `PASS / ACTUAL_EXTERNAL_QUESTION_DENY_CLICK`
- 실행 시각: `2026-08-13T20:25:51+09:00`
- 격리 경계: current-source API `127.0.0.1:18482`, current production Web `127.0.0.1:14182`, disposable DB `daon_r1_m8_09_external_it_20260813_1322`.
- 자격 보호: 기존 노출 가능성이 있던 일회성 자격은 폐기했다. 이번 Gate는 새 일회성 자격을 사용했으며 login/password, session Cookie, DB credential 원문을 증거·로그에 기록하지 않았다.
- Current build: Next `BUILD_ID=FZJb-HrGGUU3CI0sk-8Y8`, build manifest SHA-256 `E9840832CEC356256975F20171D2DCCE03F747EF27A759EE8EF9DB4C119887BF`. Source/standalone 첫 chunk hash가 일치했고 `/_next/static/chunks/0h5h93w1h3szb.js`는 HTTP 200이었다.

## Database와 production fixture

- PostgreSQL 15 disposable DB에 Migration `0001→0012`를 적용했다.
- `0012→0011→0012` rollback/reapply로 fixture tenant/workspace의 deterministic deny backfill을 적용했다.
- Production helper는 `PostgresSourceUploadService → PostgresDocumentProcessingRepository.start(enqueue=True) → PostgresDocumentProcessingQueue.claim → processing.complete → PostgresDocumentIndex.index_result → queue.complete` 순서를 사용했다.
- API/Web 기동 전 실제 상태는 `source=ready`, `processing=completed`, `document_job=completed`였다.
- external_api Provider/Deployment/text binding은 각각 1개이며 current Workspace `deny_external` binding은 1개였다.

## 실제 Chrome와 same-origin Network

- matching static을 포함한 production standalone Web에서 React login control 활성화를 확인했다.
- Chrome 새 격리 tab에서 정식 login/session 후 same-origin `/workspaces/{workspace_id}`로 이동했다.
- `GET /bff/api/workspaces/{workspace_id}/sources`의 downstream API 결과는 200이고 `external-gate.pdf`가 visible/enabled, selected 상태였다.
- 병렬 Studio outputs는 503 `STUDIO_DATABASE_UNAVAILABLE`였지만 독립 safe warning으로 표시됐고 Source 선택과 질문 입력은 보존됐다.
- 비민감 질문을 입력하고 enabled 상태의 `질문 실행` 버튼을 실제로 1회 클릭했다.
- same-origin `POST /bff/api/workspaces/{workspace_id}/questions`의 downstream API 결과는 403이며 화면에는 safe code `EGRESS_POLICY_DENIED`만 표시됐다. 내부 URL, stack, credential, Cookie, token은 노출되지 않았다.

## Deny durability와 transport

- `runs=1`, `egress_decisions(allowed=false)=1`, `routing_decisions=1`.
- `model_attempts=0`, `run_results=0`.
- `audit_events(action=question.egress.authorize,outcome=denied)=1`.
- bounded provider transport spy value는 `0`이었다.

## 판정

Workspace effective deny가 실제 Chrome Question click에서 fail-close로 작동했다. Run/EgressDecision/RoutingDecision과 deny Audit은 provider 호출 전에 durable commit됐고, provider transport·ModelAttempt·RunResult는 모두 0이다. 요구된 actual external Question deny Browser Gate를 완료했다.

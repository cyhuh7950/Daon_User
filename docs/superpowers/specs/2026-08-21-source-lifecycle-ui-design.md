# Source 수명주기 UI 상세 설계

## 승인과 범위

- Issue: `R1-M8-10-SOURCE-LIFECYCLE-UI-I006`
- 승인일: 2026-08-21
- 현재 `codex/user-auth-screen-split`은 확인된 `origin/master`보다 0 behind/37 ahead이며 운영 Notebook·Source 기준선을 포함한다. 어울1 판단으로 I006의 임시 통합 기준선으로 사용하고, 완료 후 별도 master 통합 판단이 필요하다.
- 초기 Source 상태 분리, Notebook 연결 해제, 기존 R1-M5-06 삭제 요청 UI만 포함한다. 실제 사용자 Source 변경, 영구 Purge, 배포는 제외한다.

## 상태 소유권

- Source HTTP·parse·abort·projection 결과는 Source 상태만 갱신한다.
- Knowledge, Conversation, Studio 결과와 projection 오류는 각 패널 상태만 갱신한다.
- 각 로드는 `{session, tenant, workspace, notebook, epoch}`에 결속한다. 최신 snapshot과 일치하는 결과만 반영하며 stale success/reject/finally는 DOM·선택·오류를 변경하지 않는다.
- retryable Source 실패는 기존 bounded 1회 재시도만 허용하고 4xx/5xx safe code와 retryable 여부를 보존한다.

## Notebook에서 제거

- 일반 사용자의 기본 제거는 원본 Source·SourceVersion을 삭제하거나 비활성화하지 않는다.
- 공개 계약은 `POST /api/v1/workspaces/{workspace_id}/notebooks/{notebook_id}/source-unbindings`이다. `source_id`, `source_version_id`를 exact 입력으로 받고 `Idempotency-Key`, `If-Match`를 요구한다.
- 현재 Binding은 물리 수정·삭제하지 않는다. 신규 append-only unbinding 원장으로 종료 사실을 기록하고 모든 selected-context/list/question/studio 조회는 유효 Binding만 투영한다.
- Tenant·Workspace·Notebook·SourceVersion scope, RLS, Audit, ETag, Idempotency와 동시 요청을 한 PostgreSQL transaction에서 검증한다.

## Source 삭제 요청

- R1-M5-06의 기존 request/get/cancel API와 30일 유예, Legal Hold 우선, 복구 계약을 그대로 사용한다. Browser는 derivative inventory/reference를 입력하지 않으며 서버가 current tenant/workspace/source 정본에서 exact 6종을 산출·고정한다.
- authoritative inventory가 누락되거나 scope가 다르면 request·source state·audit write0으로 fail-close한다.
- UI는 `Notebook에서 제거`와 `Source 삭제 요청`을 분리하고 확인 대화상자에서 영향 범위를 명시한다.
- 삭제 요청 성공 즉시 비활성화·유예/hold/cancel 가능 상태만 표시한다. 영구 Purge는 호출하지 않는다.
- Browser는 same-origin BFF만 사용하고 관리자·Step-up 등 기존 서버 권한을 우회하지 않는다.

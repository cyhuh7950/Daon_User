# Notebook 영구 삭제 설계

## 목적

사용자가 Notebook Home에서 특정 Notebook을 선택해 확인한 뒤, 해당 Notebook에 귀속된 사용자 데이터와 Object Storage 파일을 영구 삭제한다. 삭제 요청은 장시간 작업과 부분 실패를 고려해 비동기 작업으로 수행하고, 화면에는 진행 상태와 최종 결과를 표시한다.

## 범위

포함:

- Notebook Home 카드의 삭제 메뉴와 확인 UI
- Workspace/Notebook 범위 권한 검증
- 삭제 요청 생성 및 상태 조회 API
- Notebook과 연결된 Source·처리·생성·산출물·인덱스·Object Storage 데이터 삭제
- 실패 시 재시도 가능한 삭제 작업과 감사 이벤트
- 삭제 완료 후 Notebook Home 목록에서 제거

제외:

- Workspace 자체 삭제
- 다른 Notebook이 공유하는 Source/Object의 삭제
- 조직 보존 정책 또는 법적 보존(hold)을 우회하는 삭제
- 사용자가 직접 실행하는 DB/CLI 삭제 명령

## 삭제 의미와 보존 원칙

삭제 대상 Notebook의 전용 데이터는 영구 삭제한다. 다만 보안·감사·법적 추적에 필요한 최소 감사 이벤트(삭제 주체, 대상 Notebook ID, 요청/완료 시각, 결과 코드)는 별도 감사 저장소에 남긴다. Notebook이 삭제되면 일반 Notebook 목록·상세·질문·Studio 생성 API는 `RESOURCE_UNAVAILABLE`을 반환한다.

Source 또는 Object가 다른 Notebook에서 참조되는 경우에는 공유 객체를 삭제하지 않고 해당 Notebook의 바인딩만 제거한다. 참조 여부를 판정할 수 없는 경우 삭제 작업을 중단하고 `DELETE_SHARED_DATA_BLOCKED`로 기록한다.

## 사용자 흐름

1. Notebook Home 카드의 `⋮` 메뉴에서 `노트북 삭제`를 선택한다.
2. 확인 모달에 Notebook 제목과 삭제 대상(연결된 Source·산출물·파일)을 표시한다.
3. 사용자가 Notebook 제목을 정확히 입력하고 삭제를 확정한다.
4. 클라이언트는 idempotency key와 함께 삭제 요청을 제출한다.
5. API는 권한·보존 정책·중복 요청을 검증하고 `202 Accepted`와 `deletion_request_id`를 반환한다.
6. Home 목록에서는 해당 카드를 `삭제 중`으로 표시하고 중복 삭제를 막는다.
7. 클라이언트가 상태 API를 폴링해 `completed` 또는 `failed`를 표시한다.
8. `completed`이면 카드를 제거하고 목록을 다시 조회한다. `failed`이면 안전한 오류 코드와 재시도 안내를 표시한다.

## API 계약

### 삭제 요청

`DELETE /api/v1/workspaces/{workspace_id}/notebooks/{notebook_id}`

필수 헤더:

- `Idempotency-Key`: 기존 API의 안전한 재요청 형식
- `If-Match`: Notebook metadata version 또는 목록 응답의 ETag

응답:

- `202`: `{ "data": { "deletion_request_id": "...", "status": "accepted" }, "meta": { "trace_id": "..." } }`
- `401/403`: 인증·권한 부족
- `404`: Notebook이 없거나 이미 완료 삭제됨
- `409`: 버전 충돌 또는 이미 삭제 진행 중
- `423`: 법적 보존 또는 공유 데이터 삭제 차단

### 상태 조회

`GET /api/v1/workspaces/{workspace_id}/notebooks/{notebook_id}/deletion-requests/{deletion_request_id}`

상태는 `accepted`, `deleting`, `completed`, `failed` 중 하나이며, 실패 시 사용자에게 노출 가능한 `safe_error_code`만 반환한다.

## 서버 처리

삭제 요청 트랜잭션은 삭제 작업 레코드와 감사 이벤트를 먼저 기록한 뒤 큐에 내보낸다. Worker는 다음 순서로 처리한다.

1. Notebook 범위와 보존 정책을 재검증한다.
2. Notebook binding을 스냅샷하고 다른 Notebook 참조 여부를 확인한다.
3. 진행 중인 문서 처리·생성 작업을 취소 가능한 상태로 전환한다.
4. Notebook 전용 결과·인덱스·처리 파생 데이터를 외래키 의존성의 역순으로 삭제한다.
5. Notebook 전용 Source 버전과 Source 레코드를 삭제한다.
6. 참조가 남지 않은 Object Storage 객체를 삭제하고 object record를 정리한다.
7. Notebook metadata, binding, activity, idempotency 및 삭제 작업의 내부 상태를 정리한다.
8. 감사 이벤트에 완료 결과를 기록하고 상태를 `completed`로 전환한다.

각 단계는 작업 상태와 재시도 횟수를 기록한다. DB 단계와 Object Storage 단계가 불일치하면 삭제 작업을 실패로 끝내지 않고, 남은 단계부터 재개할 수 있는 보정 작업으로 전환한다.

## 데이터 안전성

- 기존 immutable 테이블에 일반 `DELETE` 권한을 부여하지 않는다.
- 범위 검증을 포함한 전용 삭제 함수/서비스만 삭제를 수행한다.
- 모든 SQL은 `tenant_id`, `workspace_id`, `notebook_id`를 함께 조건으로 사용한다.
- 공유 Source/Object는 참조 카운트가 0일 때만 삭제한다.
- 삭제 전 법적 보존·보존 기간·진행 중 작업을 확인한다.
- 감사 이벤트에는 원문·문서 내용·비밀값을 저장하지 않는다.

## UI 오류 처리

- `accepted/deleting`: 카드에 `삭제 중` 배지와 진행 상태 표시
- `completed`: 카드 제거 후 목록 재조회
- `DELETE_SHARED_DATA_BLOCKED`: 공유 데이터가 있어 삭제를 중단했다는 안내
- `RETENTION_HOLD`: 보존 정책으로 삭제할 수 없다는 안내
- `WORKSPACE_REQUEST_FAILED`: 재시도 버튼 제공

## 검증 기준

- 삭제 요청이 중복 제출되어도 하나의 삭제 작업만 생성된다.
- 권한이 없는 Workspace/Notebook 삭제가 차단된다.
- Source·산출물·인덱스·Object Storage가 삭제되고 다른 Notebook의 공유 데이터는 보존된다.
- Worker 중단 후 재시작하면 마지막 성공 단계부터 재개된다.
- 완료 후 Notebook Home 목록과 상세 URL에서 해당 Notebook이 노출되지 않는다.
- 감사 이벤트에 요청·완료·실패 정보가 남는다.
- 브라우저는 same-origin BFF 상대 경로만 사용한다.

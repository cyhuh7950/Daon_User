# Source 수명주기 UI 작업계획

1. 정본·Git·보호 dirty와 기존 Source/NotebookBinding/R1-M5-06 계약을 기록한다.
2. React deferred/reverse RED로 Source 결과 소유권과 monotonic epoch를 고정하고 최소 GREEN한다.
3. append-only Source unbinding을 Domain→Migration0021→PostgreSQL Repository→Runtime/OpenAPI→same-origin BFF/client→UI 순으로 RED→GREEN한다.
4. 기존 R1-M5-06 request/get/cancel에서 서버가 exact 6종 inventory를 current Source 정본으로 산출하게 하고, same-origin client/UI에 연결하여 request/grace/hold/cancel 및 purge0을 검증한다.
5. disposable PostgreSQL에서 fresh migration, RLS/cross-scope, stale ETag, idempotency, concurrency, selected context/question/studio filtering을 검증한다.
6. 관련 API/Node/OpenAPI/Web build·TS·boundary·secret scan·diff-check·staged0를 마감한다.

Commit·Push·배포와 실제 사용자 Source write는 독립 검토 및 별도 사용자 승인 전 수행하지 않는다.

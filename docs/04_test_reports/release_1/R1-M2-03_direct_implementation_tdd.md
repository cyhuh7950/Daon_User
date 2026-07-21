# R1-M2-03 직접 구현 TDD 증거

## 범위

- 원 작업: `R1-M2-03-I001`
- 실행 주체: 어울1 · `DIRECT_IMPLEMENTATION`
- 승인: 동일 작업 `INCOMPLETE 3/3` 도달 후 신산님 승인
- RED Commit: `39c6dd2`
- GREEN Commit: `be908d0`
- 상태 조합 RED Commit: `98906c7`
- 상태 조합 GREEN Commit: `3183845`

## 사용자 여정과 보장

| # | 사용자 관점 보장 | Test | RED | GREEN |
| --- | --- | --- | --- | --- |
| 1 | 검토 요청 뒤 Source 목록과 선택 상세가 같은 `needs_review` 상태를 표시한다. | `검토 요청 후 Source 목록과 선택 상세는 같은 needs_review 상태를 표시한다` | `projectSourceState` 부재로 실패 | 단일 투영 함수 적용 후 PASS |
| 2 | Parser/OCR 불일치는 `needs_review`이며 실제 `failed`·`expired`는 복구 진입을 공개한다. | `Parser/OCR 불일치는 needs_review이며 failed·expired는 다음 복구 진입을 공개한다` | 실제 `failed` 대 기대 `needs_review`로 실패 | 분기 정정·복구 진입 추가 후 PASS |
| 3 | 검토자는 충돌 심각도를 상향하고 해결·상향 행동을 Audit Preview에서 확인한다. | `충돌 검토자는 심각도를 상향하고 해결·상향 행동을 Audit Preview에서 확인한다` | `informational`이 상향되지 않아 실패 | 상향 전이·Audit 연결 후 PASS |
| 4 | Source 사용 중지는 `disabled`와 `active=false`를 함께 적용하고 복구 Control을 숨긴다. | `사용 중지 Source는 disabled 비활성 상태이며 복구 진입을 노출하지 않는다` | 상태가 `partial`로 남고 복구 Control 노출 | 상태 전이·표시 계약 정합 후 PASS |
| 5 | 해결된 충돌의 심각도를 다시 높이면 해결을 재개하고 최종화를 다시 차단한다. | `해결된 충돌의 심각도 상향은 검토를 다시 열고 최종화를 차단한다` | `resolved`가 유지되어 최종화 차단 미복구 | `unresolved`·`review_reopened` 전이 후 PASS |

## 실행 증거

- RED: `node --test scripts/tests/source-knowledge.test.mjs` → 19건 중 기존 16 PASS, 신규 3 FAIL.
- 1차 GREEN: 같은 명령 → 19/19 PASS.
- 상태 조합 RED: 20건 중 기존 18 PASS, 신규 2 FAIL.
- 최종 GREEN: 같은 명령 → 20/20 PASS.
- 통합: `npm run verify:workspace` → 34/34 PASS.
- Lint: `npm run lint:workspace` → 11 files PASS.
- Foundation: `npm run verify:product-foundation` → 8/8 PASS.
- Toolchain: `npm run verify:toolchain` → PASS.
- Independence: `npm run verify:independence` → components 8, edges 10, violations 0.
- Production Build: `npm run build --workspace @daon-user/web` → Exit 0.
- 공통 Gate: `npm run verify:quality-gate` → 7범주 PASS, Failures 0, Exit 0.

## 실제 Browser 검증

Production Next 서버의 `/workspaces/release-one`에서 실제 클릭했다.

- 부분 이해 Source의 `검토 요청` 후 선택 목록과 상세가 모두 `검토 필요`; Audit `review_requested · needs_review` 표시.
- Parser/OCR 불일치 Source는 목록·상세·ProcessingRun 모두 `needs_review`; 양쪽 결과 위치 보존 표시.
- 실제 `failed` Source는 `재처리 진입 · unavailable`, `expired` Source는 `재등록 진입 · unavailable` 표시.
- Source 사용 중지 후 목록·상세가 `disabled`, `active=false`, 복구 Control 0건이며 `source_disabled` Audit 표시.
- `material → resolved → critical` 순서 뒤 해결 상태가 `unresolved`로 재개되고 `review_reopened`, `review_required`, 최종화 3종 차단 표시.
- Browser Console warning/error 0건.

## Coverage와 제한

저장소에는 별도 Coverage 명령이 없다. 이번 변경의 순수 상태 전이와 사용자 표시 계약은 신규 5건과 기존 Source·Workspace 34건에서 직접 실행했다. 실제 API·DB·LLM·재처리·재등록은 원 작업 범위대로 `unavailable`이며 M2-07·M6 이후 연결 대상이다. Browser Resource Timing 목록은 현재 Browser Sandbox 제한으로 수집하지 않았다.

# R1-M4-01 OpenAPI v1 공통 계약 작업지시서

## 승인 기준

- 기준 Branch `codex/r1-m4-01`, 기준 HEAD `400ff07b83452e7c8267ff00bbbb5118d94502b3`.
- 승인 설계 정본 `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` v0.7과 Release 1 구현계획 v0.9, 테스트계획 v0.7을 적용한다.
- `packages/contracts`는 공개 Schema를 소유하는 Leaf 원천이며 이번 작업의 단일 Writer는 어울2다.

## 단일 목표

이후 Web BFF·Native Gateway·FastAPI가 동일 의미로 구현할 Versioned OpenAPI v1 공통 계약을 `packages/contracts`에 고정한다. Runtime/API Server를 구현하지 않는다.

## 허용 산출물

- `packages/contracts/openapi/v1/openapi.json`과 같은 Package 내부 공통 예제
- `packages/contracts/package.json`, `packages/contracts/README.md`의 최소 Export·사용 계약
- `docs/01_architecture/openapi_v1_common_contract.md`
- `scripts/verify-openapi-contract.mjs`, `scripts/tests/openapi-contract.test.mjs`
- Root `package.json`의 `verify:openapi-contract` Script
- `docs/03_evidence/release_1/R1-M4-01/openapi-contract-summary.json`
- 본 작업지시서·Prompt·Progress·결과보고

App·Service·UI·Runtime·Workflow·Lockfile·Provider SDK와 외부 의존성을 변경하지 않는다.

## OpenAPI 3.1 정본 계약

1. `openapi`는 3.1.x, `info.version`은 `1.0.0`이며 설계 §17.1의 `/api/v1/...` 전체 Path를 누락 없이 포함한다. 모든 Operation은 고유 `operationId`, Tag, Summary와 후속 구현 소유를 가진다.
2. Server absolute URL, localhost·127.0.0.1·Docker/Internal Provider/Worker 주소와 Secret 이름·값을 금지한다. `servers`는 생략하거나 same-origin relative 의미만 사용한다.
3. 모든 Resource ID는 의미 추론이 없는 공통 opaque string Schema를 사용하고 UUID·ULID 형식을 고정하지 않는다. Path ID는 공통 Parameter Ref를 사용한다.
4. 목록 Query는 공통 `cursor`, `limit`, `filter`, `search` Parameter를 재사용한다. 응답은 `items`, `next_cursor`를 가지며 임의 도메인 정렬 정책은 확정하지 않는다.
5. 모든 응답은 `X-Trace-Id`를 가진다. Versioned mutable Resource 응답은 `ETag`, 중복 실행 가능한 POST Write는 `Idempotency-Key`, 기존 Resource PATCH/DELETE는 `If-Match`를 사용하며 409·412 안전 충돌 응답을 공통 Ref로 표현한다. 권한·소유권 Enforcement 구현은 M4-03·04 소유다.
6. 공통 성공 Envelope와 안전 오류 Envelope를 정의한다. 오류는 `code`, 사용자용 `message`, `stage`, `impact`, `retryable`, `user_action`, `trace_id`를 포함하고 Stack·DB/Internal Host·Provider Raw Error·Secret 이름을 금지한다.
7. 필수 Code `COST_LIMIT_EXCEEDED`, `STEP_UP_REQUIRED`, `CURRENT_ACCESS_DENIED`, `IMPORTANT_KNOWLEDGE_CONFLICT`, `NO_AVAILABLE_UNDERSTANDING_MODEL`, `NO_AVAILABLE_DEPLOYMENT`를 포함한다. COST·STEP_UP·CURRENT_ACCESS·NO_AVAILABLE_DEPLOYMENT는 승인된 안전 Typed Details를 제공하고 내부 식별자·원문을 포함하지 않는다.
8. `/api/v1/runs/{id}/events`는 `text/event-stream`, event `id/type/occurred_at/trace_id/payload` Schema와 `Last-Event-ID` 재연결 계약을 사용하며 Worker·Provider 원문을 노출하지 않는다.
9. 주요 Path에는 설계 역할에 맞는 최소 GET/POST/PATCH/DELETE Operation을 두고 모든 Operation이 공통 Trace/Error Response를 재사용한다. 도메인 Payload는 opaque/versioned Resource와 후속 소유 경계 이상으로 임의 완성하지 않는다.

## 검증기·TDD·증거

- 외부 라이브러리 없이 Node로 JSON Parse, Version, 전체 Path, unique operationId, `$ref`, 공통 Header/Error/SSE, 금지 URL·Secret, Write Header 규칙을 fail-close 검증한다.
- Canonical JSON SHA-256, Path·Operation·Schema·Error Code 수와 Contract Version을 deterministic Summary로 만든다. `--write`만 증거를 갱신하고 기본/`--no-write`는 저장소를 수정하지 않은 채 기존 증거와 일치해야 한다.
- 정본·검증 계약 부재를 먼저 RED로 기록한다. GREEN 뒤 누락 Path·중복 operationId·Unsafe Error Field/Absolute URL·Write Header 누락·SSE Content 누락 Fixture가 실제 거부되는 Test를 포함한다.

## 완료 검증·전달

Targeted Contract Test, `verify:openapi-contract -- --write` 후 `--no-write`, 전체 Node, Independence, 가능한 Quality Gate, Toolchain, Workspace·Mobile·Desktop 영향 범위 회귀, JSON Parse, Package Export Import, Node Syntax, Diff와 Product/Protected Boundary를 검증한다. 환경 차단은 별도로 분류한다.

Progress는 착수·세부 단계·오류/복구·테스트·종료 직전에 Append한다. 결과보고는 `판정 → 판단 이유 → 조치` 순서와 표준 상태 형식을 사용한다. 구현·검증 완료 후 단일 목적 Commit을 `codex/r1-m4-01`에 Push하고 exact SHA·원격 일치·Clean을 보고한다. CI·PR·Merge는 어울1이 담당한다.

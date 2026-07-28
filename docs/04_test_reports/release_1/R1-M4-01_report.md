# R1-M4-01 OpenAPI v1 공통 계약 결과보고

## 판정

`COMPLETED` — 승인된 OpenAPI v1 공통 계약과 무의존 fail-close 검증기, 결정적 증거를 구현했다. 대상 검증은 모두 통과했고, 전역 Gate의 1개 실패와 Mobile iOS 10개 실패는 exact base `400ff07b83452e7c8267ff00bbbb5118d94502b3`에서도 동일 재현되어 `BASELINE_LIMITATION`으로 분리한다.

## 판단 이유

- OpenAPI `3.1.0`, 계약 Version `1.0.0`, 설계 §17.1의 36개 Path와 59개 Operation을 고정했다.
- 모든 Operation의 고유 ID·Tag·Summary·후속 구현 소유, opaque ID, 목록 Query, Trace·ETag·Idempotency·If-Match·409/412 계약을 검증한다.
- 안전 오류 6종과 Typed Details, `text/event-stream`·`Last-Event-ID`·RunEvent 계약을 포함한다.
- Absolute Server URL·내부 주소·Secret·Raw 오류 필드를 fail-close로 차단하며 Browser same-origin 경계를 문서화했다.
- 기본/`--no-write`는 증거를 변경하지 않고, `--write`만 결정적 Summary를 갱신한다.
- App·Service·UI·Runtime·Workflow·Lockfile·Provider SDK는 변경하지 않았다.

## 생성·변경 결과

- 계약 정본·공개 Export: `packages/contracts/openapi/v1/openapi.json`, `packages/contracts/package.json`, `packages/contracts/README.md`
- 검증 자동화: `scripts/verify-openapi-contract.mjs`, `scripts/tests/openapi-contract.test.mjs`, Root `package.json`
- 구조·증거: `docs/01_architecture/openapi_v1_common_contract.md`, `docs/03_evidence/release_1/R1-M4-01/openapi-contract-summary.json`
- 전달·복구 기록: R1-M4-01 Work Order, Prompt, Progress, 본 결과보고

## 테스트 결과

| 검증 | 결과 |
| --- | --- |
| OpenAPI Targeted Test | 6/6 PASS |
| `verify:openapi-contract -- --write`와 기본 no-write | PASS, 36 Paths·59 Operations·17 Schemas·6 Error Codes |
| Canonical SHA-256 | `F10EAA9D905BA13DC515074739E5329A217F60563B971DEF4410D663FAD0886C` |
| no-write 증거 파일 불변 | PASS, 파일 SHA `6FE662CED102F35D8AD021D243E8662ADDF1874012BFD528E621A3FC14FC76A8` |
| JSON Parse·Node Syntax·Package self-export | PASS, export Version 1.0.0·36 Paths |
| Independence·Toolchain | PASS, violations 0·정확 Pin 검증 |
| Workspace | 34/34 PASS |
| iOS 계약 Test를 제외한 전체 Node | 277/277 PASS |
| Desktop Lint·Build·Node Unit | PASS, Node Unit 25/25 |
| Mobile Lint·Type·공통 Unit·Contract·Android | PASS, 공통 10/10·Studio 15/15·Android 11/11 |
| Quality Gate, 20분 제한 | Exit 1, 272.1초; lint/type/contract/build/security/independence PASS, unit의 `desktop-shell-unit` 1 FAIL |

## 미해결 사항

- `BASELINE_LIMITATION`: Mobile iOS 계약 10건은 기준선과 변경 Worktree 모두 Exit 1이며 빈 Contract 절편에 대한 `assert.ok(...)` 실패다.
- `BASELINE_LIMITATION`: Desktop Rust `production_manager_error_fixtures_are_bounded_and_leave_no_processes`는 양쪽 모두 `state did not become ready`, 13/14, Exit 1이다.
- 두 실패 대상 파일은 이번 Diff에서 변경하지 않아 R1-M4-01 인과 회귀는 0건이다. 상세 Test 이름과 비교 증거는 Progress에 기록했다.
- CI·PR·Merge는 어울1 소유다.

## 조치

단일 목적 Commit을 `codex/r1-m4-01`에 Push하고 exact SHA·원격 일치·Clean을 인계한다. 기준선 iOS·Desktop 문제는 본 작업을 다시 열지 않고 별도 Work Order에서 처리한다.

## 표준 상태

`COMPLETED | R1-M4-01 | OpenAPI v1 공통 계약·검증기·결정적 증거 구현 | 36 Path·59 Operation·17 Schema·6 안전 오류 계약 | 대상 검증 PASS, 전역 실패는 exact base 동일 BASELINE_LIMITATION | CI·PR·Merge 미수행 | 어울1의 결과 검토와 PR 판단`

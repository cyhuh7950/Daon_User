# R1-M2-04-I001 개발 결과보고 · Attempt 1

## 판정

`COMPLETED · HANDOFF_READY`

## 판단 이유

- 승인된 Production-bound Prototype 범위에서 Run·모델·근거의 순수 상태 모델과 화면을 연결했다.
- Mode, Frozen RoutingContext/RunSnapshot, 결정론적 후보 정렬, Fallback, 비용 사전 차단, waiting_user/waiting_approval, 안전 오류, Citation과 중요 충돌 차단을 구현했다.
- 실제 Retrieval·Provider·LLM·DB Runtime은 실행하지 않았고, 불투명 Deployment ID와 same-origin 경계를 유지했다.
- 신규 Test 12/12, 기존 Workspace 회귀 34/34, Lint, Foundation, Toolchain, Independence, Next Production Build, 공통 7범주 Quality Gate를 통과했다.
- Production Browser 1920×1080, 1200×900, 800×900, 500×900에서 클릭 여정을 검증했다. Console warning/error, API-like 요청, 비동일 Origin 요청은 모두 0건이다.

## 수행 내용

1. `run-model-evidence-model.js`에 RunSnapshot, RoutingContext, Deployment 후보, Hard/Runtime 제외, stable ID tie-break, Attempt/Fallback, 비용·근거·오류 상태를 순수 모델로 구현했다.
2. `run-model-evidence-pane.jsx`에 Mode/Preview/6단계/분기 Fixture/결정 원장/Citation/다음 행동 UI를 구현했다.
3. Workspace 정본에 `RunViewState`를 추가해 Pane 재마운트와 네 폭 Projection 뒤에도 현재 Run을 보존했다.
4. 기존 Evidence Viewer에는 Citation이 가진 `evidenceId`를 전달하도록 연결했다.
5. Prototype Adapter 교체 계약과 M3/M4/M5/M6 후속 책임을 Architecture 문서로 고정했다.
6. TDD 중 다음 결함을 Red로 재현하고 교정했다.
   - 분리된 Run 파일이 기존 금지 URL 정적 검사에서 누락됨.
   - Citation ID가 Evidence ID 대신 전달됨.
   - waiting_user Fixture가 실패 전 상태를 열고 다음 행동을 표시하지 않음.
   - Fallback 후 결정 원장이 최초 Device 모델 계보를 계속 표시함.

## 생성·변경 결과

- 생성: `packages/ui/src/run-model-evidence-model.js`
- 생성: `packages/ui/src/run-model-evidence-pane.jsx`
- 변경: `packages/ui/src/workspace-model.js`
- 변경: `packages/ui/src/adaptive-workspace.jsx`
- 변경: `packages/ui/src/index.js`
- 변경: `packages/ui/src/workspace.css`
- 생성: `scripts/tests/run-model-evidence.test.mjs`
- 변경: `scripts/tests/workspace.test.mjs`
- 생성: `docs/01_architecture/run_model_evidence_prototype_adapter_contract.md`
- 생성: `docs/03_evidence/release_1/R1-M2-04/*`
- 생성: `docs/04_test_reports/release_1/R1-M2-04_progress.md`
- 생성: 이 결과보고
- 변경 없음: API, DB, Schema, Migration, Dependency, Lockfile, 공통 Quality Gate 코드

착수 전부터 존재한 보호 Dirty `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`, `violations.json`은 수정·복원·Stage하지 않았다.

## Test

| 검사 | 결과 |
| --- | --- |
| `node --test scripts/tests/run-model-evidence.test.mjs` | PASS · 12/12 |
| `npm run verify:workspace` | PASS · 34/34 |
| `npm run lint:workspace` | PASS · 11 files |
| 신규 2개 명시 Lint | PASS · 2 files |
| Product Foundation | PASS · 8/8 |
| Toolchain Baseline | PASS |
| Repository Independence | PASS · components 8, edges 10, violations 0 |
| Next Production Build | PASS |
| Common Quality Gate | PASS · 7 categories, Failures 0 |

Windows OneDrive 환경에서 Build/Gate 상위 명령이 `next build` 자식 종료 전에 반환하는 현상이 있어 Process Tree와 `.next/lock`을 추적했다. 자식을 강제 종료하지 않고 자연 종료와 `lock=False`를 확인한 뒤 최종 Gate 결과를 판정했다. Gate가 자동 갱신한 기존 R1-M1-05 증거 2개는 결과 확인 후 HEAD로 복원했다.

## Browser 증거

| Viewport | 검증 | 결과 |
| --- | --- | --- |
| 1920×1080 | three-pane, pinned, 6단계 completed, Citation 계보, Escape Focus 복원 | PASS |
| 1200×900 | two-pane, TIMEOUT Fallback 2 Attempt, 최종 External 모델 계보 | PASS |
| 800×900 | single-pane, 비용 사전 차단, 자동 재시도·미완성 결과 0, Pane 왕복 보존 | PASS |
| 500×900 | bottom-tabs, waiting_user 두 행동, Frozen Snapshot 불변, 중요 충돌 최종 확정 차단 | PASS |

- Console warning/error: 0/0
- Browser Resource Timing API-like 요청: 0
- 비동일 Origin 요청: 0
- 금지 내부 주소 요청: 0
- `prototype_contract_passed`: `true`
- `runtime_not_executed`: `true`

상세 값은 `docs/03_evidence/release_1/R1-M2-04/browser-validation.json`, Screenshot과 `evidence-manifest.json`에 기록한다.

## 미해결 사항

- 실제 Retrieval·Provider·LLM·DB Runtime과 서버 API 검증은 승인 계약상 이번 M2 Prototype 범위가 아니므로 실행하지 않았다.
- S9 Commit·Push·ysna-server 격리 검증과 S10 독립 검토·Merge는 어울1 책임 범위로 남아 있다.

## 조치

S8 `HANDOFF_READY`에서 구현·증거 쓰기를 중지한다. 어울1은 Diff와 증거를 검토한 뒤 Commit·Push 및 S9/S10 진행 여부를 판단한다.

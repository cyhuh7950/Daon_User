# R1-M2-04-I001 개발 결과보고 · Attempt 2

## 판정

`COMPLETED · HANDOFF_READY`

## 판단 이유

- C01에서 지적된 8개 검토 항목(C2-1~7·C3-1)을 독립 Test와 새 화면 증거로 재현한 뒤 구현과 화면을 보정했다.
- 정렬 Comparator, Hard/Runtime 제외, Exhaustion Terminal, 비용 사전 차단, Fallback 최종 계보, Frozen Preview를 Test와 Browser 증거로 확인했다.
- 신규·회귀 Test 18/18, Workspace 34/34, Lint, Foundation, Toolchain, Independence, Next Production Build, 공통 7범주 Quality Gate가 통과했다.
- 새 Browser 세션과 5개 Screenshot에서 1920×1080, 1200×900, 800×900, 500×900 상태를 다시 검증했다.

## 수행한 작업

1. 후보 정렬을 `privacy_tier → minimum_quality → locality_preference → reliability → latency → cost → current_load → stable_deployment_id` 전체 Comparator로 구현했다.
2. Hard Filter 5종과 Runtime Filter 4종의 실제 후보 및 독립 사유 코드를 추가했다.
3. Generic/Understanding Exhaustion, pinned offline/health/capacity `waiting_user`, 인증·잘못된 요청 Fail 분기를 고정했다.
4. 비용 원장을 `0.17 + 0.03 > 0.18 USD`, `pre_attempt_cost_check`, Attempt/Retry/Incomplete Result 0으로 일치시켰다.
5. Fallback 뒤 Decision, Provider Profile, Artifact/Digest, 역할별 최종 모델, Understanding Model, Attempt가 최종 선택 Deployment를 가리키도록 보정했다.
6. 현재 Run의 완전한 Frozen RoutingContext Preview를 표시하고, 다음 Run 설정 변경 뒤에도 pinned Snapshot이 불변임을 확인했다.
7. Adapter 교체 계약에 정렬·제외·Terminal·Lineage·Cost·Network 증거 구분을 추가했다.

## 생성·변경한 결과

- 변경: `packages/ui/src/run-model-evidence-model.js`
- 변경: `packages/ui/src/run-model-evidence-pane.jsx`
- 변경: `scripts/tests/run-model-evidence.test.mjs`
- 변경: `docs/01_architecture/run_model_evidence_prototype_adapter_contract.md`
- 갱신: `docs/03_evidence/release_1/R1-M2-04/browser-validation.json`
- 갱신: 5개 Browser Screenshot 및 `evidence-manifest.json`
- 생성: 이 Attempt 2 결과보고
- 변경 없음: API, DB, Schema, Migration, Dependency, Lockfile, 배포 설정

착수 전 보호 Dirty인 `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`, `violations.json`은 수정·복원·Stage하지 않았다.

## Test

| 검사 | 결과 |
| --- | --- |
| C01 RED | 기존 12 PASS, 신규 6 FAIL · 계약 누락 재현, 환경 실패 0 |
| `node --test scripts/tests/run-model-evidence.test.mjs` | PASS · 18/18 |
| `npm run verify:workspace` | PASS · 34/34 |
| `npm run lint:workspace` | PASS · 11 files |
| Production 변경 파일 명시 Lint | PASS · 2 files |
| Product Foundation | PASS · 8/8 |
| Toolchain Baseline | PASS |
| Repository Independence | PASS · components 8, edges 10, violations 0 |
| Next Production Build | PASS · `.next/lock=False` 확인 |
| Common Quality Gate | PASS · 7 categories, Failures 0 |

명시 Lint에 Test Fixture의 금지 URL 정규식 문자열까지 잘못 포함한 첫 명령은 입력 범위 오류로 실패했다. Production 파일 2개로 대상을 바로잡아 통과했으며 제품 코드 오류는 아니었다. Build/Gate의 Next 자식은 강제 종료하지 않고 자연 종료와 Lock 해제를 확인했다. Gate가 자동 갱신한 기존 R1-M1-05 증거는 결과 확인 후 HEAD로 복원했다.

## Browser 증거

| Viewport | 검증 | 결과 |
| --- | --- | --- |
| 1920×1080 | three-pane, pinned completed, 완전한 Frozen Preview, Citation 계보·Escape Focus | PASS |
| 1200×900 | two-pane, TIMEOUT 2 Attempt Fallback, 최종 External 계보 전 항목 일치 | PASS |
| 800×900 | single-pane, 비용 계산·Checkpoint·Attempt/Retry/Result 0 동시 노출 | PASS |
| 500×900 | bottom-tabs, pinned waiting_user 두 행동·Snapshot 불변·중요 충돌 차단 | PASS |

- Console warning/error: `0/0`
- Browser Performance API: `unavailable`
- 사유: `Performance API is unavailable in the Browser evaluation context`
- 따라서 API-like, 비동일 Origin, 금지 내부 주소의 Runtime 요청 건수를 0으로 단정하지 않았다.
- 제품 소스 정적 계약 검사는 금지 절대·내부 URL, 직접 fetch, `NEXT_PUBLIC_API_BASE_URL` 사용이 각 0건이다.
- `prototype_contract_passed`: `true`
- `runtime_not_executed`: `true`

상세 DOM 단언, 시각 검수, Screenshot 크기와 Network 증거 구분은 `browser-validation.json`에 기록했다.

## 미해결 사항

- 실제 Retrieval·Provider·LLM·DB Runtime과 서버 API 검증은 승인된 M2 Prototype 범위 밖이라 실행하지 않았다.
- S9 Commit·Push·ysna-server 격리 검증 및 S10 독립 검토·Merge는 어울1 판단과 책임 범위다.

## 조치

S8 `HANDOFF_READY`로 구현·증거 쓰기를 중지한다. 어울1은 Diff, Attempt 2 보고서와 Manifest를 검토해 S9/S10 진행 여부를 판단한다.

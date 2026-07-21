# R1-M2-04-C01 수정 작업지시서 — Routing 계약·Browser 증거 정합

## 1. 판정과 작업 계약

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M2-04` |
| 수정 Work Order | `R1-M2-04-C01` |
| 동일 issue_id | `R1-M2-04-I001` |
| 누적 상태 | `INCOMPLETE 1/3` · 유효 `FAILURE_REPORT 0` |
| 판정 | `REWORK_REQUIRED` |
| 기준 Branch | `codex/r1-m2-04` |
| 기준 HEAD | `7e8a018f...` + Attempt 1 미커밋 구현·증거 |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M2-04_progress.md`에 C01 단계 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-04_attempt-2.md` |

원 작업지시서와 승인 정본 전체는 그대로 유효하다. 이 문서는 Attempt 1 검토에서 발견된 중대 미진만 교정한다. 기능 범위·공개 API·데이터 계약·보안 경계를 확장하지 않는다.

## 2. 반려 근거

자동 Test 12/12, Workspace 34/34, Manifest Hash 8/8은 통과했으나 Test가 승인 계약을 충분히 검증하지 못했고 Screenshot이 Browser JSON의 주장과 일치하지 않았다. 따라서 `COMPLETED/HANDOFF_READY`를 수용하지 않는다.

### C2-1 전체 결정론적 정렬

- 현재 후보 선택은 `id.localeCompare`만 수행한다.
- 각 Deployment Fixture에 `privacyTier`, `minimumQuality`, `localityPreference`, `reliability`, `latency`, `cost`, `currentLoad`, stable ID 비교 값을 명시한다.
- 설계서 §10.4의 순서로 실제 Comparator를 구현한다. stable deployment ID는 앞 기준이 모두 같은 때만 최종 Tie-break다.
- 앞 기준 중 하나만 달라지는 Fixture와 완전 동점 Fixture를 Test해 정렬 결과를 고정한다.

### C2-2 Hard Filter 5종·Readiness Filter 4종

- 다음 정책 제외 Code를 각각 실제 후보와 독립 Test로 만든다: 소유권/Workspace, Provider·Local-only, 역할·Modality·Context, Artifact·License, Residency·Egress.
- 다음 Runtime 제외 Code를 각각 실제 후보와 독립 Test로 만든다: Deployment/Node Ready, Artifact Digest/Installation, Credential/Provider Auth readiness, Health/Capacity/Circuit.
- 정책 제외와 Runtime 제외를 섞지 않고 RoutingDecision·원장·UI에 안전 Code로 표시한다.

### C2-3 종료 상태와 pinned Runtime 분기

- 일반 역할 Runtime 후보 소진은 `failed/NO_AVAILABLE_DEPLOYMENT`다.
- Source 의미 이해 역할 Runtime 후보 소진은 `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`다.
- `pinned`의 Offline·Health·Capacity는 무단 모델 변경 없이 `waiting_user`와 `재시도 | 허용 모델 변경`을 제공한다.
- 인증 오류·잘못된 요청은 우회 없이 재시도 불가 `failed`다.
- 각 경로를 상태·오류 Code·Attempt 수·다음 행동 Test로 고정한다.

### C2-4 비용 차단 원장 정합

- Preflight 입력 `accumulatedCost=0.17`, `estimatedNextCost=0.03`, limit `0.18 USD`를 그대로 Ledger와 화면에 표시한다.
- `costBlockedAt`에 `pre_attempt_cost_check`를 기록한다.
- Attempt 0, `policy_blocked/COST_LIMIT_EXCEEDED`, 미완성 Result 0, 동일 Frozen Context 자동 재시도 0을 함께 검증한다.
- 비용 차단 Screenshot에는 오류 Code·0.17+0.03/0.18·차단 시점·다음 행동이 실제로 보여야 한다.

### C2-5 Fallback 최종 계보 정합

- Fallback 뒤 `RoutingDecision.selectedDeploymentId`, 선택 이유, Provider Profile, Deployment, ModelArtifact, Digest, 역할별 최종 모델, Understanding Model과 ModelAttempt가 모두 최종 선택과 일치해야 한다.
- 최초 실패 Attempt는 불변으로 보존한다.
- UI 원장·Domain 상태·Screenshot의 최종 External 모델을 상호 대조한다.

### C2-6 Frozen Preview 완전성

Preview에 현재 RunSnapshot의 다음 필드를 표시한다.

- Mode와 local scope 또는 pinned Deployment
- Actor·Tenant·Workspace
- 역할·분류·데이터 영역·Egress
- Routing/Workspace 정책 버전·기한
- 비용 한도·통화·적용 범위
- 허용 후보·정렬·Fallback 계획
- SourceVersion·권위·가중치 계층/요청/유효/Clamp·RuleSet
- Prompt·Tool 계약 버전

`waiting_user`에서 다음 Run 설정을 바꾼 뒤에도 현재 Preview가 pinned Snapshot으로 남는 Browser·Reducer Test를 추가한다.

### C2-7 상태별 Browser 증거 재촬영

- `run-fallback-1200x900.jpg`: Fallback Fixture 선택, Attempts 2개, 최초 Device 실패와 최종 External 계보가 화면에 보인다.
- `run-cost-blocked-800x900.jpg`: 대화·실행 Pane에서 `COST_LIMIT_EXCEEDED`, 비용 값·차단 시점·재시도 금지·미완성 0이 보인다. Studio Pane 캡처를 정본으로 사용하지 않는다.
- `run-waiting-user-500x900.jpg`: waiting_user Fixture가 선택되고 오류·두 다음 행동·현재 pinned Snapshot이 보인다.
- `run-conflict-500x900.jpg`: 중요 충돌·partial 근거·최종 확정 차단이 보인다.
- 각 Screenshot 직전 DOM에서 선택 Fixture, Run status, 핵심 계보/비용/행동을 읽고 Paint 대기 후 촬영한다. Browser JSON의 값과 이미지 내용을 시각 검수한다.

### C3-1 Network 증거 구분

- `resource_timing_entries`에는 실제 전체 Resource Timing 수를 기록한다.
- `api_like_requests`, `non_same_origin_requests`, `forbidden_internal_address_requests`를 별도 Filter 결과로 기록한다.
- Prototype API 요청 0건과 Next 정적 Asset/Document Resource 존재를 혼동하지 않는다.
- Browser Timing API를 읽지 못하면 `unavailable`과 이유를 기록하며 0으로 위장하지 않는다.

## 3. TDD·검증 순서

1. 위 C2-1~6을 각각 재현하는 실패 Test를 Production 수정 전에 추가하고 유효 RED를 확인한다.
2. 순수 모델부터 최소 교정하고 전용 Test를 Green으로 만든다.
3. Workspace·M2-02·M2-03 회귀, Lint, Foundation, Toolchain, Independence, Production Build, 공통 7범주 Gate를 다시 수행한다.
4. 최종 Production Build로 Browser 네 폭을 새 세션에서 검증한다. 이전 Screenshot·JSON·Manifest를 정본으로 재사용하지 않는다.
5. Architecture 계약, Browser JSON, Screenshot, Manifest, Progress, Attempt 2 보고서를 최종 상태로 갱신한다.

## 4. 완료조건

- C2-1~6 신규 회귀 Test가 실제 값·전이·원장 필드를 검증하고 전부 PASS
- 원 작업 전체 자동 Gate PASS, Console warning/error 0
- 상태별 Screenshot과 Browser JSON의 Fixture·status·핵심 값 일치
- 전체 Resource 수와 API-like·비동일 Origin·금지 주소 수 구분
- Manifest 실제 Hash 100% 일치, JSON Parse, `git diff --check`, 추적 삭제·Lockfile 변경 0
- 보호 R1-M1-04 Dirty 2개 미수정·미복원·미Stage
- 결과보고가 Attempt 1의 반려 사유와 교정 근거를 숨기지 않음

## 5. 허용 범위와 중지 조건

허용 범위는 기존 R1-M2-04 구현·Test·Architecture·Evidence·Progress·Attempt 2 보고다. 공통 Gate·CI·Dependency·Lockfile·API·DB·Schema는 변경하지 않는다. 다른 작업자와 병렬 코드를 수정하지 않는다.

S8 `HANDOFF_READY`에서 쓰기를 중지한다. 기능 범위·요구사항·공개 API·데이터 계약·보안 경계·중요 위험 변경이 필요하면 구현을 중지하고 어울1에게 증거와 선택지를 보고한다.

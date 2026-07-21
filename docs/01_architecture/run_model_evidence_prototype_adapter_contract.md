# Run·모델·근거 Prototype Adapter 승계 계약

## 판정

`R1-M2-04`는 M3가 재사용할 대화·실행 Pane의 Production-bound 상태·표현 기준선이다. 결정론적 Fixture와 순수 Reducer만 실행하며 실제 API·DB·LLM·Provider·Retrieval Network는 실행하지 않는다. 화면은 `프로토타입 데이터`와 `Prototype · unavailable`을 유지하고 실제 성공으로 위장하지 않는다.

## 소유 경계

| 경계 | 현재 소유 | 후속 교체 책임 |
| --- | --- | --- |
| Run·Frozen RoutingContext·RoutingDecision·ModelAttempt·비용·Citation 순수 모델 | `packages/ui/src/run-model-evidence-model.js` | M5 저장 정본과 M6-02·M6-10·M6-14 실제 Routing·Run Adapter |
| Mode·진행·결정 원장·안전 오류·근거 상태 표현 | `packages/ui/src/run-model-evidence-pane.jsx` | M3 Client Shell이 Component와 접근성 계약을 그대로 재사용 |
| Layout·Pane·Drawer·Evidence Viewer·Focus·RunViewState 보존 | M2-02 `AdaptiveWorkspace`와 `WorkspaceViewState` | M3 Shell 유지, M4 BFF 연결 |
| SourceVersion·Evidence 위치·권위·가중치·충돌 | M2-03 Source 정본을 Snapshot으로 참조 | M6-09 Citation 원문 재현 |

## 불변 Run 계약

- 실행 시작 시 Actor·Tenant·Workspace·Mode·역할·데이터 분류·영역·Egress 정책·정책 버전·기한·비용 한도·SourceVersion·권위·가중치·RuleSet·Prompt·Tool 계약과 허용 후보·정렬·Fallback 계획을 깊은 불변 Snapshot으로 고정한다.
- 실행 중 화면 설정 변경은 다음 Run 설정만 바꾸며 현재 Snapshot과 ModelAttempt를 수정하지 않는다.
- 상태는 `accepted → planning → retrieving → generating → validating → completed`와 `waiting_user | waiting_approval | policy_blocked | failed | cancelled`를 구분한다. `waiting_approval`은 실행 전 정책 승인 전용이며 OutputVersion 승인 대기가 아니다.
- Hard Filter는 소유권·Workspace, Provider·Local-only, 역할·Modality·Context, Artifact·License, Residency·Egress 5종을 독립 Code로 분리한다. Runtime Readiness는 Deployment·Node, Artifact Digest·Installation, Credential·Provider Auth, Health·Capacity·Circuit 4종을 별도 Code로 분리한다.
- 정책·Runtime Filter를 통과한 후보는 `privacy tier → minimum quality → locality preference → reliability → latency → cost → current load → stable deployment ID` 순서로 비교한다. stable ID는 앞 7개 값이 모두 같을 때만 사용한다.
- `auto` Fallback은 Frozen Policy·역할·영역·Egress 안의 Timeout·Rate Limit·일시 장애·Capacity에만 허용한다. 인증·잘못된 요청·정책·Egress 거부, Local-private→External, Stream 이어쓰기, `pinned` 무단 모델 변경은 금지한다.
- 일반 역할 Runtime 후보 소진은 `failed/NO_AVAILABLE_DEPLOYMENT`, Source 의미 이해 역할 소진은 `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`이다. 허용된 `pinned`의 Offline·Health·Capacity는 `waiting_user`와 재시도·허용 모델 변경을 제공한다.
- Fallback 뒤 RoutingDecision·Provider Profile·Deployment·ModelArtifact·Digest·역할별 최종 모델·Understanding Model·ModelAttempt는 마지막 선택과 일치하고 최초 실패 Attempt는 보존한다.
- 비용 한도 도달은 호출 전에 `policy_blocked/COST_LIMIT_EXCEEDED`로 종료하며 동일 Frozen Context 자동 재시도와 미완성 결과 노출은 0건이다.
- 비용 사전 차단 원장은 누적·예상·한도·통화와 `costBlockedAt=pre_attempt_cost_check`, Attempt 0을 보존한다.

## Citation·Evidence 계약

- Citation은 `RunSnapshotId | SourceId | SourceVersionId | EvidenceId | 위치 | 문맥 | 인용`을 함께 보존하고 M2-02 Evidence Viewer를 연다. 별도 Viewer 정본을 만들지 않는다.
- 근거 상태는 `sufficient | partial | insufficient`를 Icon과 텍스트로 함께 표시한다.
- 해결되지 않은 `material | critical` 충돌은 `IMPORTANT_KNOWLEDGE_CONFLICT`와 검토 진입을 표시하고 최종 결과 확정을 차단한다.

## Browser·API 경계

- 이번 Prototype의 실제 API 요청은 0건이다. Browser 증거는 전체 Document·정적 Asset Resource 수와 API-like·비동일 Origin·금지 내부 주소 Filter 수를 별도로 기록하며, Timing API를 읽을 수 없으면 `unavailable`과 이유를 기록한다.
- Browser 상태에는 불투명 Deployment ID와 안전한 표시 이름만 두며 Raw Provider URL·Code·Secret·내부 Host를 두지 않는다.
- M4 BFF Adapter는 same-origin 상대 경로만 사용한다. `localhost`, `127.0.0.1`, Docker 내부 Host·Port, API 절대주소와 `NEXT_PUBLIC_API_BASE_URL` Client Fetch는 금지한다.

## 후속 책임

- M3: 현재 Component·상태·반응형·접근성 Shell 승계.
- M4: same-origin BFF·공개 API 계약과 안전 오류 응답.
- M5: Run·Snapshot·RoutingDecision·ModelAttempt·Citation 불변 저장 정본.
- M6-02·M6-14: 실제 Registry·Routing·Provider·Fallback·비용 실행.
- M6-09: 실제 Retrieval·Citation·SourceVersion 원문 재현.
- M6-10: 초기 Web Thin Vertical E2E에서 UI·Route·Network·Lineage 일치 검증.

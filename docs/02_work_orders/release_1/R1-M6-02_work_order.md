# R1-M6-02 CP3 Core Routing·Egress 작업지시서

## 승인 기준과 Writer

- 버전 `1.0` · 2026-08-01. Work Order `R1-M6-02`, Issue `R1-M6-02-I001`.
- 공식 작업공간은 `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`, 현재 Branch `codex/r1-m5-07`이다.
- 승인 정본은 `AGENTS.md`, 상세 설계 §10.1~§10.6·§16.1·§17·§18.2·§20.2, 구현계획 §6·§8.1·§15·§21~§24, 테스트계획, Baseline Manifest, R1-M6-01 결과를 EOF까지 읽는다.
- 어울1은 직접 구현 인수를 유지하며 단일 Writer다. 보호 Untracked 2개와 `D:\Project\Daon_User`는 건드리지 않는다.

## 단일 목표와 계약

- R1-M6-01의 승인 Deployment를 대상으로 고정 `RoutingContext`와 `EgressDecision`을 만들고, 단일 모델 선택·실행 Attempt·RunResult 계보를 보존한다.
- RoutingContext에는 Actor·Tenant·Workspace·Mode·요구 역할·Data Realm·외부 전송 정책·고정 Deployment·Policy Version·비용 한도·지식/RuleSet Snapshot ID를 포함한다.
- Hard Filter는 소유권·권한·Workspace·Provider/Local-only·Data Realm·Egress 정책·Health·역할을 순서대로 적용한다. 탈락 이유를 기록하고 후보가 없으면 `policy_blocked`로 종료한다.
- `EgressDecision`은 목적지·전송 범위·분류·Byte·마스킹·정책·승인 주체·Provider/Model을 기록한다. Local-private의 External 자동 전환은 차단한다.
- 자동 Fallback은 구현하지 않는다. 동일 Frozen Context에서 다른 모델을 자동 선택하거나 재시도하지 않으며, 비용 한도 초과는 `policy_blocked/COST_LIMIT_EXCEEDED`다.
- 실제 실행은 `ModelAttempt`와 `RunResult`에 Deployment·Artifact Digest·Policy Version·EgressDecision·성공/실패 사유를 연결한다. Provider 미가용은 `failed/NO_AVAILABLE_DEPLOYMENT`로 Fail-close한다.

## 허용·제외 범위

- 허용: 내부 RoutingContext·Hard Filter·EgressDecision·ModelAttempt·RunResult 모델과 테스트, R1-M6-01 Registry Adapter 연계.
- 제외: 자동 Fallback(M6-14), Managed Local Model 설치(M6-03), Source 이해·Retrieval·Connector·RuleSet 구현, 공개 API 추가/변경, 실제 외부 Provider 호출과 서버 배포.
- 공개 Route·OpenAPI·SafeError·데이터 계약·보안 경계를 바꿔야 하면 즉시 `BLOCKED`로 보고한다.

## TDD와 완료 증거

1. RED: Local-private→External 차단, 역할/Realm/권한/Health 불일치 차단, 후보 없음, 비용 한도, Frozen Context 자동 Fallback 금지, EgressDecision 누락, Digest 불일치, 미가용 Deployment를 실패로 고정한다.
2. GREEN: 단일 승인 Deployment의 고정 Route와 계보·Egress·비용 Fail-close를 최소 구현한다.
3. 회귀: R1-M6-01 전용·API 전체·OpenAPI·same-origin 금지 패턴·`py_compile`·`git diff --check`를 실행한다.
4. 진행 파일 `docs/04_test_reports/release_1/R1-M6-02_progress.md`에 착수·RED·GREEN·오류/복구·테스트·Commit을 기록한다.
5. 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 및 `판정 → 판단 이유 → 조치` 순서로 보고한다. CP3 전체 E2E 완료로 보고하지 않는다.

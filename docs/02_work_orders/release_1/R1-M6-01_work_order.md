# R1-M6-01 CP3 Core Model Registry·Adapter 작업지시서

## 승인 기준과 Writer

- 작업지시서 버전: `1.0` · 2026-08-01.
- Work Order ID: `R1-M6-01`; Issue ID: `R1-M6-01-I001`.
- 공식 작업공간: `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`.
- Branch: `codex/r1-m5-07`에서 후속 구현을 수행하되, 어울2가 유일 Writer다. 기준 HEAD는 착수 시 기록한다.
- 승인 정본: `AGENTS.md`, `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.9의 §8.2·§10.1~§10.4·§16·§17·§18.1~§18.2·§21.1·§23.1·§25, `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.2의 §6·§8.1·§15·§21~§24, `docs/04_test_reports/release_1_test_plan.md` 0.7, `docs/01_architecture/DECISIONS.md`의 관련 R1-D 결정, `docs/02_work_orders/release_1_baseline_manifest.json`을 EOF까지 읽고 적용한다.
- 선행 Work Order: `R1-M5-04` Cloud/Local Canon·`R1-M5-04`의 Model/Provider Projection, `R1-M5-07`의 외부 데이터·BFF 경계를 재사용한다. M5-07의 인증 세션 미검증은 이 작업의 성공으로 소급해 닫지 않는다.
- `D:\Project\Daon_User`와 `C:\tmp`는 수정·삭제·작업 전환하지 않는다. 보호 Untracked 2개는 수정·삭제·Stage하지 않는다.

## 단일 목표와 완료 조건

- 목표: CP3에서 사용할 **단일 승인 Vision/문서입력 LLM Deployment**를 제품 정본에 등록하고, Artifact·Deployment·Binding 최소 계약과 Adapter 실행/Health 검증을 제공한다.
- Registry는 `provider_profile`, `model_artifact`, `model_deployment`, `model_binding`의 불투명 ID·Version·상태·역할·Data Realm·Egress Policy·Digest를 안전하게 투영한다. Secret·Token·내부 Host·원문 Credential은 저장·응답·Evidence에 남기지 않는다.
- Model 역할은 이 작업에서 `vision` 또는 설계가 허용하는 문서 입력 의미 이해 역할로 고정한다. Parser/OCR를 의미 이해 Model로 등록하거나 Model 부재를 Parser-only 성공으로 대체하지 않는다.
- Deployment는 `registered → validating → ready`와 `disabled | unhealthy | rejected`를 엄격히 구분한다. Digest·Contract Version·Health Timestamp가 없는 배포는 `ready`가 아니다.
- Binding은 Workspace/Policy Scope와 역할을 고정하며, 승인되지 않은 후보·다른 Data Realm·외부 전송 정책 위반·Digest 불일치는 Fail-close한다. 자동 Fallback·자동 Provider 교체·전역 Active Mapping 참조는 구현하지 않는다.
- Adapter는 고정 Deployment에 실제 입력을 전달하고 구조화된 이해 결과와 Evidence/ModelAttempt에 연결 가능한 Digest·Run ID·Health를 반환한다. 실제 Provider가 없는 환경에서는 명시적 `UNAVAILABLE`/`NO_AVAILABLE_DEPLOYMENT`로 종료하며 성공 Mock으로 가장하지 않는다.

## 허용·제외 범위

- 허용: 기존 Model Deployment OpenAPI/Domain/Repository/Adapter 경계 보강, 최소 Registry Projection·Health 계약, 테스트 Fixture와 Contract/Integration Test, 진행·Evidence 문서.
- 제외: 자동 Routing·Fallback(후속 M6-02/M6-14), Local Model 설치·Update·Rollback(M6-03), Source 파싱·Vision 이해 파이프라인(M6-05/M6-06), Retrieval·Connector·RuleSet, 새로운 공개 SafeError Code, 외부 운영 배포.
- 기존 공개 API·데이터 계약·보안 경계를 변경해야 하면 코드를 수정하지 말고 `BLOCKED`로 보고한다. 기존 OpenAPI의 `listModelDeployments`와 충돌하면 어울1에게 증거를 회부한다.

## TDD와 필수 검증

1. RED: 승인되지 않은 역할/Realm/Binding, Digest 누락·불일치, Health 만료, disabled/unhealthy Deployment, Secret·내부 주소 노출, Mock 성공 경로, 자동 Fallback 시도를 먼저 실패로 고정한다.
2. GREEN: 승인 단일 Deployment 등록·조회·Health·Adapter 입력/출력·계보 Snapshot과 Fail-close를 최소 구현한다.
3. Contract/OpenAPI: 기존 Route와 Schema를 대조하고 공개 API 추가·변경이 없는지 확인한다.
4. Runtime: 실제 Adapter가 실행 가능한 환경에서는 실제 입력·출력·Digest·Health를 검증한다. 불가능하면 명시적 미가용 상태와 재현 명령을 남긴다.
5. 회귀: M4 Auth/Step-up/Audit, M5-01~07 관련 API·OpenAPI·Quality Gate·독립성 검사를 실행한다. Browser 코드는 same-origin 상대 경로만 사용하며 내부 API 주소·localhost·`NEXT_PUBLIC_API_BASE_URL` 직접 호출이 0건인지 정적 확인한다.

## CP3 경계와 증거

- 이 작업은 CP3의 선행 Core Registry이며 단일 PDF 전체 E2E를 완료했다고 보고하지 않는다. CP3 본 검증은 `R1-M6-10`에서 수행한다.
- Evidence에는 exact Commit SHA, Registry Projection, Artifact/Deployment/Binding 상태, Digest·Health·실제/미가용 Adapter 결과, SafeError, 테스트 명령과 결과를 연결한다.
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M6-01_progress.md`.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환하며 검토 출력은 `판정 → 판단 이유 → 조치` 순서로 고정한다.
- 착수·각 단계 완료·오류/원인/복구·각 테스트·종료 직전에 진행 파일을 갱신한다. `COMPLETED`는 필수 산출물·변경 파일·완료 근거가 모두 있을 때만 사용한다.

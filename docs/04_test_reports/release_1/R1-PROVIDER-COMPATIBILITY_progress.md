# R1 Provider 호환 프로토콜 진행 기록

- 일자/담당: 2026-10-03 / 어울1
- 기준: `codex/next-user-development`, 시작 HEAD `494518b4`, 시작 시 clean.
- 단계/상태: 신산님이 OpenAI Chat Completions·Anthropic Messages와 모델 ID 직접 입력+실제 시험 방향 승인. 상세 설계 문서 작성, 문서 검토 승인 대기. 구현 미착수.
- 변경 파일: `docs/superpowers/specs/2026-10-03-provider-compatible-protocols-design.md`, 이 진행 기록.
- 읽기 전용 근거: 기존 `adapter_type` 저장, `CUSTOM`의 OpenAI 고정 Adapter, `provider_code` 기준 질문 실행, 신규 비공용 CUSTOM 모델 등록 미완료 기록을 확인.
- 테스트: 문서 placeholder·diff 형식 점검만 수행. 제품 단위·통합·브라우저·실 Provider 시험 미실행.
- 오류 횟수: 0. 기존 작업의 오류 횟수와 합산하지 않음.
- 미검증/위험: 실제 Provider별 호환 범위와 비용, 사용자 Key 흐름, WSL 개발 URL, 기존 연결 보존. 모델별 시험 호출에는 실제 Provider 사용료가 발생할 수 있음.
- 다음 조치: 신산님이 상세 설계 문서를 검토·승인하면 구현 계획을 작성하고 별도 계획 검토를 받는다. 승인 전 제품 코드·DB·배포 변경 없음.

## 설계 승인 및 구현 계획

- 단계/상태: 2026-10-03 신산님이 상세 설계를 승인. `docs/superpowers/plans/2026-10-03-provider-compatible-protocols.md` 작성·자체 검토 중; 계획 검토 승인 전 구현 미착수.
- 담당/변경 파일: 어울1 / 구현 계획과 이 진행 기록. 앞선 설계 commit은 `257735de`.
- 테스트: 코드 테스트·실 Provider·브라우저·WSL 미실행. 계획의 파일·인터페이스·테스트 명령을 현 checkout과 대조하고 문서 형식을 점검.
- 오류 횟수: 0. `daon_user_api`는 src-layout이므로 Windows 테스트에서 `PYTHONPATH=services/api/src`가 필요함을 읽기 전용 import 확인으로 재확인.
- 미검증/다음 조치: 계획의 기술적 구현 가능성과 전체 회귀는 아직 미검증. 신산님 계획 검토 후 단일 제품 코드 writer에게 인계; WSL 배포는 별도 gate.

## Task 1 구현 시작 — R1-PROVIDER-COMPAT-20261003-T1

- 일자/담당: 2026-10-03 / 어울2(daon-developer), 단일 제품 코드 writer.
- 기준/상태: `codex/next-user-development`, 시작 HEAD `de5c4542bd51093010d6a52a52ce578fff43eba8`, clean 확인. 신산님 인계에 따라 승인된 Task 1만 착수.
- 시작 점검: PMO·프로젝트 지침, 승인 설계(`257735de`)와 계획(`de5c4542`), 이 진행 기록 및 대상 코드·테스트 확인. 기존 `CUSTOM`은 목록 조회로만 `verify()`하며 신규 `verify_models()` 계약은 없음.
- 변경 파일 예정: `provider_connection_adapters.py`, `provider_catalog.py`, 두 대상 테스트 및 이 진행 기록.
- 변경 전 테스트: `PYTHONPATH=services/api/src`에서 두 대상 pytest 파일 `35 passed`.
- 오류 횟수: 0. RED 단계의 의도된 실패는 구현 오류 횟수에 포함하지 않음.
- 미검증: 실 Provider/비용 호출, DB·Web·WSL·배포는 Task 1 범위 밖. 전체 API 회귀는 구현 후 확인.
- 다음 조치: 두 규격 조회·모델별 실제 probe·안전 실패 테스트를 먼저 추가하고 RED를 관찰.

### Task 1 RED

- 테스트 변경: 두 규격 `/models` 경로·헤더, 조회 404 후 수동 ID 시험, 16토큰 본문, 2xx 형식 불량, 두 번째 모델 실패, redirect·timeout·SSRF, 비지원 Adapter 차단, 검증 모델 정규화를 추가.
- 실행 결과: 대상 pytest `22 failed, 35 passed`. 신규 실패는 `AdapterRegistry.adapter()`의 타입 인수 미지원, `verify_models()`·`from_verified_text_models()` 부재로 확인. 기존 테스트 실패 없음.
- 오류 횟수: 0(의도한 RED).
- 변경 파일: 두 대상 테스트와 이 진행 기록. 다음 조치: 계획 Step 3 최소 구현 후 같은 명령 GREEN 확인.

### Task 1 GREEN 및 자체 검토

- 구현: `AdapterRegistry.adapter(provider_code, adapter_type="")`의 `CUSTOM` 규격 분기, 기본 Adapter의 `verify_models()` 안전 차단, 두 규격의 `/models` 헤더와 모델별 비스트리밍 16토큰 probe, 최소 text 응답 검증, 시험 성공 모델의 `DiscoveredModel` 정규화.
- 변경 파일: `services/api/src/daon_user_api/provider_connection_adapters.py`, `provider_catalog.py`, `services/api/tests/test_provider_connection_adapters.py`, `test_provider_catalog.py`, 이 진행 기록(총 5개). 인접 제품 파일 변경 없음.
- GREEN 증거: 대상 2개 pytest 파일 최종 `61 passed`; 조회 404/405·양 규격 인증 실패를 보강한 7개 Provider 인접 파일 묶음 `114 passed, 1 skipped`(skip: 실제 PostgreSQL DSN 없음). `git diff --check` exit 0. RED의 신규 실패 22개는 모두 해소.
- 보안·회귀 확인: fixture transport의 요청에서 `CUSTOM` HTTPS URL 검증, SSRF 입력의 요청 전 차단, redirect 미추적, 5초 timeout, Key·upstream 오류 원문 미노출, 모델별 최대 1회 요청을 확인. 기존 Ollama·Gateway·OPENROUTER·GROQ·MISTRAL·UPSTAGE·MEDIA_BRIDGE·SENTENCE_TRANSFORMERS 관련 테스트 통과. 실제 Key·외부 Provider 호출 0회.
- 자체 검토: 승인 설계/Task 1 인터페이스와 변경 diff를 대조. 저장·개인 Key·질문 실행·Web·DB 계약은 다음 Task 범위로 남김. Critical/Important finding 없음.
- 오류 횟수: 0. 의도한 RED는 실패 횟수에 산입하지 않음.
- 미검증: 실제 Provider별 응답/비용, DB 불변, Web/Network, WSL·배포는 Task 1 밖. PostgreSQL DSN 필요 테스트 1개 미실행.
- 다음 조치: Task 1 관련 5개 파일만 commit 후 어울1에게 결과 인계. Task 2~5는 이번 어울2 범위 밖.

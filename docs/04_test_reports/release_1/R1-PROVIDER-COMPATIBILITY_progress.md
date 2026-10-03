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

### Task 1 인계 체크포인트

- 코드·테스트·진행 기록 commit: `7697bc2d4c9041034ca99efd5144cac9508ad404`(지정된 5개 파일만 포함). 커밋 직후 worktree clean 확인.
- 최종 판정: Task 1 로컬 구현·fixture 검증 완료. 자체 검토에서 남은 Critical/Important finding 없음. 실제 Provider 연결 성공 또는 DB 저장 완료를 주장하지 않음.
- 다음 조치: 어울1이 Task 2 관리자 preview/시험-후-저장 연계를 검토·진행. 이번 인계에서 push·PR·merge·배포 없음.

## Task 2 구현 시작 — R1-PROVIDER-COMPAT-20261003-T2

- 일자/담당: 2026-10-03 / 어울2(daon-developer), 동일 단일 writer.
- 기준/상태: `codex/next-user-development`, HEAD `7e786327ae4fa85b1089b3024ddc1ddde9a46abc`, clean 확인. 신산님이 Task 1 인계 수락 및 Task 2만 재지시.
- 설계/계획: 승인 설계와 구현 계획 전체 재독. Task 2 대상은 관리자 read-only preview, 모델별 시험 후 원자 저장, 공용 Key 암호화·비공용 시험 Key 비저장, 저장 규격 불변과 replay digest.
- 변경 전 테스트: `PYTHONPATH=services/api/src`에서 대상 `test_provider_connection_admin.py`·`test_provider_settings_runtime_http.py` `25 passed`.
- 변경 파일 예정: `provider_connection_admin.py`, `runtime.py`, 두 대상 테스트, 이 진행 기록. 오류 횟수 0.
- 미검증: 실제 Provider·유료 호출·실 DB·Web·WSL·배포는 이번 범위 밖. 다음 조치: fixture 기반 RED 테스트 작성 및 실패 원인 확인.

### Task 2 RED 및 Task 1 검토 수신

- RED: 신규 관리자 서비스/HTTP fixture 테스트 8개가 예상대로 실패하고 기존 25개는 통과. 실패 원인은 `test_credential` command 필드와 `model-preview` route 부재로 확인. 의도된 RED이므로 구현 오류 횟수 0.
- 신산님이 전달한 내부 Task 1 검토: 모델별 시험 총량 상한, OpenAI 토큰 파라미터 호환, Anthropic 모델 목록 pagination. Task 2 GREEN 후 완료 판정 전 공식 문서·승인 설계를 대조하고 Task 1 보완을 별도 테스트/commit으로 분리한다. 외부 수락 결과로 취급하지 않음.
- 다음 조치: 기존 admin auth/step-up/version/replay 흐름을 유지하며 Task 2 최소 구현.

### Task 2 GREEN 체크포인트 — Task 1 검토 보완 전

- 구현: 관리자 전용 step-up `provider_catalog.preview` 경로(읽기 전용), `CUSTOM` 두 규격별 실제 모델 probe 후 create/update/공용 Key 교체, 비공용 일회성 시험 Key 비저장, keyed digest replay, 저장된 규격 변경 409 차단. 조회 실패 또는 모델별 시험 실패 시 기존 연결·Key·카탈로그·허용 목록 불변.
- 검토 경계: `CUSTOM` catalog refresh 성공 시 조회 목록에서 빠진 수동 검증 모델을 stale 처리하는 현상을 신규 RED로 확인하고, refresh에서 기존 catalog를 보존하도록 수정. 조회만으로 verification 상태를 새로 올리지 않음.
- 변경 파일: `provider_connection_admin.py`, `runtime.py`, `test_provider_connection_admin.py`, `test_provider_settings_runtime_http.py`, 이 진행 기록(총 5개).
- 검증: 대상 2개 pytest 파일 `39 passed`; Provider 인접 8개 파일 묶음 `139 passed, 1 skipped`(실 PostgreSQL DSN 없음). `git diff --check`는 commit 직전 재확인 예정. fixture transport만 사용, 외부 Provider·비용 호출 0회.
- 오류 횟수: 구현 중 비의도 실패 원인 2개(테스트 가짜 DB의 `Jsonb` 객체 동일성 비교, preview step-up의 operation/key 쌍 누락)를 각각 확인·수정. 신규 Secret 반사 및 refresh stale 실패는 의도된 RED; 동일 근본 원인 3회 반복 없음.
- 미검증: 실 DB transaction/Provider, 사용자 Key Task 3, Web Task 4, WSL/배포. Anthropic 모델 목록 pagination은 아직 1페이지 조회로 부분 목록 가능; Task 1 검토 보완에서 명시 기록.
- 다음 조치: Task 2 지정 파일만 체크포인트 commit 후, 신산님 전달 Task 1 Important(모델 수 상한) RED→GREEN과 토큰 파라미터·pagination 판단을 별도 commit으로 처리. Task 3 시작 금지.

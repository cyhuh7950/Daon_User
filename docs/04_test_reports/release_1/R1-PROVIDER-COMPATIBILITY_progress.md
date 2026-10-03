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

### Task 2 체크포인트 및 Task 1 내부 검토 보완

- Task 2 체크포인트: 지정 5개 파일만 `13b8d90d`에 commit. `git diff --cached --check` 통과. 사용자 지적의 refresh 성공·부분 목록에서 수동 검증/허용 모델 보존은 OpenAI·Anthropic fixture로 검증.
- Task 1 Important 상한 RED: 5개 모델 ID에 대해 catalog 1건과 두 규격 adapter 2건이 예상대로 실패; 당시 adapter는 첫 네트워크 probe로 진행. GREEN: `CUSTOM` 검증 모델을 최대 4개로 제한해 입력 검증 단계에서 `PROVIDER_MODEL_IDS_INVALID`로 종료. 5개 경계에서 두 규격 모두 네트워크 요청 0회; 4개는 허용. 5초/모델 기준 probe 요청은 최대 4회이며 기존 Provider의 모델 목록 제한은 바꾸지 않음.
- Task 1 Important 토큰 파라미터 판단: OpenAI Chat Completions 공식 문서는 `max_tokens`를 deprecated/o-series 비호환으로 명시하지만 Mistral·Upstage 공식 Chat API는 `max_tokens`를 요청 필드로 명시한다. 승인된 범용 `openai_compatible` 설계에서 단일 파라미터로 모두를 보장할 근거가 없어 16토큰 `max_tokens`를 유지하고 자동 재시도·추가 API 하위유형을 도입하지 않음. OpenAI o-series는 알려진 호환성 한계로 남겨 별도 설계 판단 전 지원 성공을 주장하지 않음. 근거: https://platform.openai.com/docs/api-reference/chat/getMessages , https://docs.mistral.ai/api , https://console.upstage.ai/api/chat .
- Task 1 Minor pagination 판단: Anthropic 공식 `/models`는 기본 20개/페이지, `has_more`·`last_id` 커서를 제공한다(https://platform.claude.com/docs/en/api/models/list). 현재 preview/refresh는 첫 페이지만 조회하므로 목록은 부분 결과일 수 있다. 이번 범위에서 추가 페이지 요청·공개 응답 계약 변경 없이 이 제한을 명시하며, 수동 ID 입력과 실제 probe는 계속 가능하고 refresh는 누락된 기존 검증/허용 모델을 stale 처리하지 않는다. Task 4 UI에서 목록이 전체가 아닐 수 있음을 표시할 필요가 있다.
- 보완 변경 파일: `provider_catalog.py`, `test_provider_catalog.py`, `test_provider_connection_adapters.py`, 이 진행 기록. 보완 RED는 의도된 3건; 새 비의도 오류 0건. 누적 비의도 오류 원인 2건, 동일 원인 3회 반복 없음.
- 검증: `PYTHONPATH=services/api/src`에서 Provider 관련 8개 파일 `142 passed, 1 skipped`(실 PostgreSQL DSN 없음). fixture transport만 사용했고 실제 Key·외부 유료 Provider 호출 없음. 최종 diff/self-review 및 commit 뒤 worktree 상태 확인 예정.
- 미검증/다음: 실제 Provider 규격 차이, 실 DB transaction, 사용자 Key Task 3, Web Task 4, WSL/배포. Task 1 보완만 별도 commit 후 Task 2 결과를 어울1에 인계; Task 3은 어울1 검토 전 시작하지 않음.

### Task 2 인계 상태

- 상태: Task 2 로컬 구현·fixture 검증 완료, 어울1 검토 대기. Task 2 commit `13b8d90d`, 분리된 Task 1 내부 검토 보완 commit `75c639ee`.
- 최종 재검증: 승인 시작점 `7e786327` 대비 `git diff --check` 통과, Provider 인접 8개 파일 `142 passed, 1 skipped`(실 PostgreSQL DSN 없음). 커밋 직후 worktree clean 확인.
- 오류 횟수: Task 2 비의도 오류 원인 2건 해결, Task 1 보완 비의도 오류 0건. 미검증은 실 Provider/유료 호출, 실 DB, Web, 사용자 Key, WSL·배포. 다음 조치: 어울1 자체 검토 및 Task 3 진행 여부 판단; 어울2는 Task 3 시작하지 않음.

### Task 2 내부 검토 P1 보완 — 자동 상태 점검의 CUSTOM 보존

- 기준/상태: 2026-10-03 / 어울2 단일 writer, `codex/next-user-development` HEAD `3231e38a`, 시작 시 clean. 어울1의 내부 읽기 전용 검토에서 P1을 전달받아 Task 3 전에 보완.
- 확인된 원인/영향: `check_active_connection()`이 `CUSTOM`의 저장된 `adapter_type`을 무시하고 이름 기준 Adapter의 `/models` 조회로 `verify()`한다. 수동 ID를 실제 생성 시험으로 검증했어도 선택적 목록 조회가 405이면 자동 점검이 `verification_status='failed'`로 내려 Workspace 모델 선택에서 제외될 수 있다.
- 결정: `CUSTOM`의 예약 상태 점검은 기존 `skipped` 결과만 반환하고 외부 호출·검증 상태 DB UPDATE를 하지 않는다. 마지막 명시적 모델별 시험 결과를 보존한다. 기존 비-CUSTOM 상태 점검의 성공/실패 갱신은 그대로 둔다. `ProviderHealthMonitor`는 서비스의 `skipped` 문자열을 그대로 결과에 담는 계약이며 상태 점검 설정·schema·auth는 변경하지 않는다.
- RED: 공용 CUSTOM OpenAI/Anthropic 수동 허용 모델 2건에서 `/models` 405 fixture가 실제 `failed`로 변경되는 것을 확인. 기존 Ollama 성공/실패 2건은 같은 실행에서 통과(`2 failed, 2 passed`). 의도된 RED로 오류 횟수에 산입하지 않음.
- GREEN: CUSTOM 2건에서 반환 `skipped`, 네트워크 0회, Provider 설정 DB 갱신 0회, 저장된 연결·모델·허용 목록 불변 확인. 기존 Ollama 성공/실패 각각 1회 목록 조회와 상태 갱신 확인. 집중 4건 통과, 인접 9개 파일 최종 `149 passed, 1 skipped`(실 PostgreSQL DSN 없음).
- Preview 표현 정정: 관리자 model-preview는 Provider 설정 DB의 연결·카탈로그·허용 목록을 쓰지 않는다. 관리자 인증의 step-up 권한은 별도 인증 저장소에서 소비되므로 시스템 전체의 DB 무기록 호출이라는 뜻은 아니다. HTTP 테스트는 step-up 재사용 403을 확인한다.
- 변경 파일: `provider_connection_admin.py`, `test_provider_connection_admin.py`, 이 진행 기록만. 이번 보완 비의도 오류 0건(이전 Task 2 누적 2건 유지). 실 DB/Provider/비용 호출, WSL·배포, Task 3·Web은 미검증/미착수. 다음 조치: 관련 회귀와 diff 자체 검토 후 범위 파일만 commit, 어울1 재검토 대기.
- 인계 체크포인트: 지정 3개 파일 commit `91ba2fc2ae7a6bc9cc0d7a2ede093a564a15e168`. 커밋 후 인접 9개 파일 `149 passed, 1 skipped`, 기준 `3231e38a` 대비 `git diff --check` 통과, worktree clean 확인. 상태는 Task 2 P1 보완 로컬 완료·어울1 재검토 대기; Task 3 시작 금지.

## Task 3 시작 및 RED — R1-PROVIDER-COMPAT-20261003-T3

- 일자/담당: 2026-10-03 / 어울2, 동일 단일 writer. 승인된 Task 3만 수행한다. 지시된 시작점 `91ba2fc2`와 달리 실제 시작 HEAD는 `34f89b60`이며, 그 사이 변경은 이 진행 기록뿐이다. 시작 worktree clean, 기존 worktree 격리 확인.
- 승인 설계/계획 및 PMO·프로젝트 지침 전체 재독. 범위는 계획에 열거된 source 5개, 테스트 5개, 이 진행 기록뿐이다.
- 변경 전 대상 5개 pytest 파일: `12 failed, 45 passed, 3 subtests passed`. 실패 12건 모두 기존 `test_identity_support.py:40`의 `NameError: user_id` 공통 HTTP fixture 오류로 Task 3 코드 변경 전부터 발생. 범위 밖 helper는 수정하지 않으며 Task 3 PASS로 간주하지 않는다.
- 신규 RED: 4개 핵심 테스트 파일 합동 실행에서 새 Anthropic adapter 미정의로 수집 오류. 분리 실행에서는 `4 failed, 37 passed`: CUSTOM 개인 Key의 `adapter_type`/허용 모델별 시험 누락, resolver 규격 미전달, registry 분기 부재가 예상 원인이다. 의도된 RED로 비의도 오류 횟수 0.
- 다음 조치: 개인 Key의 DB 허용 모델 probe 선행, resolver 규격 전달, Anthropic Messages 일반·근거 질문과 제한 헤더 transport를 최소 구현. 실제 Provider/Key·실 DB·Web·WSL·배포는 검증하지 않는다.

### Task 3 GREEN 및 자체 검토

- 구현: 개인 CUSTOM Key는 해당 연결 DB 허용 모델을 읽어 저장된 규격의 `verify_models`를 모두 통과한 뒤에만 암호화 저장한다. 실패 시 기존 Key/version은 그대로 둔다. 기존 비-CUSTOM 개인 Key 검증 경로는 유지한다.
- `ResolvedModel.adapter_type` 후방 호환 필드와 DB 조회를 연결했다. `CUSTOM/anthropic_compatible`의 일반·근거 질문만 Messages로 실행한다. 요청은 `/messages`, `x-api-key`·`anthropic-version`, 최상위 `system`/`messages`/`max_tokens`; 응답 text 블록과 usage를 기존 일반 답변 및 인용·증거 검증으로 처리한다. OpenAI/Ollama/OmniRoute/Gateway/UPSTAGE 분기는 변경하지 않는다.
- 새 transport는 서버 생성 Anthropic 헤더 두 개만 허용하고 Key의 길이·문자와 timeout을 제한한다. 기존 2 MiB 응답 상한, no-redirect, 원문/Secret 없는 upstream 오류 처리를 재사용한다. fixture에서 추가 헤더·개행 Key를 요청 전에 거부했다.
- 어울1 추가 검토의 미지원 `CUSTOM.adapter_type` 조용한 OpenAI fallback은 RED 1건으로 확인 후, 두 질문 모드에서 `TEXT_PROVIDER_UNAVAILABLE`로 fail-closed 했다. 빈 레거시 기본값과 `openai_compatible`은 기존 Chat Completions를 유지한다.
- RED 증거: 신규 adapter 수집 오류와 분리 실행의 `4 failed, 37 passed`(예상 기능 부재), 미지원 규격 `1 failed`(예상 fallback). GREEN: Task 3 핵심 4개 파일 `55 passed, 7 subtests passed`; Provider·질문·문서 인접 10개 파일 `171 passed, 7 subtests passed`. `test_question_answering_runtime_http.py`는 별도 재실행에서도 기존 `test_identity_support.py:40`의 `NameError: user_id`로 `12 failed`; 이번 변경 전과 같은 원인이라 PASS라고 하지 않는다.
- 변경 파일: Task 3 지정 source 5개, target test 4개, 이 진행 기록(10개). 비의도 구현 오류 0건; 의도된 RED와 기존 HTTP baseline은 오류 횟수에 산입하지 않음. `git diff --check` exit 0.
- 미검증: 실제 Provider/유료 호출, 실제 DB transaction 및 데이터, HTTP 대상의 정상 fixture 통과, Web/Network, WSL/배포. 다음 조치: Task 3 범위 파일만 commit 및 clean 확인 후 어울1 검토 대기. Task 4 시작 금지.
- 계획의 5개 대상 파일 합동 최종 실행도 `12 failed, 55 passed, 7 subtests passed`: 실패 12건은 모두 위 동일 `test_identity_support.py:40`의 사전 존재 `NameError`다. 테스트를 수정하거나 실패를 통과로 표시하지 않았다.
- Git stage 진단 1건: 진행 기록이 추적 파일이지만 ignore 디렉터리 하위라 명시 경로 `git add`가 exit 1을 반환했다. 확인 결과 승인 범위 10개 파일만 모두 stage됐고 유실·범위 외 stage는 없다. `git add -u`로 진행 기록 최종 갱신을 반영해 검증한다. 구현/테스트의 비의도 오류 0건과 별도 계산한다.

## Task 4 중단 및 Task 3 독립 검토 보완 — R1-PROVIDER-COMPAT-20261003-T3-REVIEW

- 일자/담당: 2026-10-03 / 어울2, 지정 worktree의 단일 코드 writer. Task 4 착수 시 HEAD `c6e10df0`, clean 확인. Task 4 조사 중 신산님 중단 지시를 받아 작업을 멈췄으며 Task 4 파일 수정·커밋은 0건이다. Task 4 재개는 어울1 재검토 지시 이후로 제한한다.
- Task 4 변경 전 Node 묶음은 기존 `provider-endpoint-ui-contract.test.mjs`의 오래된 UI 문구 assertion으로 실패했다. 화면·BFF 수정은 하지 않았고 이 실패의 교정은 Task 4 재개 후 수행한다.
- 독립 검토 원인: migration 0050은 기존 `CUSTOM` 행의 `adapter_type`을 `CUSTOM`으로 채우는데 Task 3의 Adapter/질문 실행 검증과 관리자 수정 정책이 이를 거부했다. Anthropic Messages의 JSON 필드도 공통 질문 파서의 `str()`·`bool()` 변환 때문에 잘못된 타입을 답변으로 승인할 수 있었다.
- 보완 경계: 기존 `CUSTOM` 값만 레거시 OpenAI Chat Completions로 처리하고 미지원 신규 규격은 계속 거부한다. 신규 CUSTOM 생성은 두 명시 규격만 허용하며 저장된 `CUSTOM` 행의 동일 규격 관리자 수정만 허용한다. Anthropic 일반/근거 답변의 `answer` 문자열, 근거 ID 문자열 배열, `insufficient` boolean 및 필드 구성을 검증한다. 기존 OpenAI 파싱 방식은 유지한다.
- RED: 신규 레거시 probe, 관리자 수정, 질문 분기와 Anthropic 잘못된 필드 타입에서 `10 failed, 111 passed, 7 subtests passed`를 관찰했다. 신규 CUSTOM의 레거시 규격 생성 거부와 미지원 규격 fail-closed는 별도 회귀로 보존한다. 의도된 RED 외 새 비의도 구현 오류 0건.
- GREEN 및 인접 회귀: 집중 5개 파일 `122 passed, 15 subtests passed`; Provider/관리자/개인 Key/Workspace/질문/0050 migration 인접 9개 파일 `176 passed, 15 subtests passed`. `git diff --check` exit 0. 레거시 개인 Key는 허용 모델로 Chat Completions probe 후에만 저장하는 fixture를 추가했다.
- 기존 실패 분리: `test_question_answering_runtime_http.py`는 12건 모두 변경 전과 동일한 `test_identity_support.py:40`의 `NameError: user_id`로 실패. 범위 밖 helper 수정 없이 FAIL로 유지하며 HTTP 런타임 통과를 주장하지 않는다.
- 변경 파일: `provider_connection_adapters.py`, `provider_connection_admin.py`, `question_answering.py`, `question_answering_service.py`, 대응 테스트 5개와 이 기록. 외부 Provider 호출·실 DB·WSL·push·배포 0건. 실제 Provider 호환성과 운영 데이터 불변은 미검증이다.
- 다음 조치: 위 Task 3 보완 파일만 별도 scoped commit 후 어울1 독립 재검토. Task 4 UI/BFF는 중단 유지.

## Task 4 UI/BFF 재개 — R1-PROVIDER-COMPAT-20261003-T4

- 일자/담당: 2026-10-03 / 어울2 단일 writer. 어울1이 Task 3 보완 commit `f92455f2`를 독립 재검증한 뒤 신산님이 Task 4 재개를 지시했다. 재개 시 branch `codex/next-user-development`, HEAD `f92455f2`, worktree clean 확인. API/DB/WSL/원격 변경 없음.
- RED: 신규 UI 2건은 `호환 방식` 입력 부재로 실패, preview API helper는 메서드 부재, BFF exact POST는 405로 실패. 기존 `provider-endpoint-ui-contract`에는 현재 구현과 반대인 step-up 금지/오래된 문구 assertion이 있어 수정 전 실패했다. 의도된 RED와 기존 stale assertion을 분리했다.
- GREEN 구현: 신규 CUSTOM 두 명시 규격(`openai_compatible`/Chat Completions, `anthropic_compatible`/Messages) 선택, Provider 이름 자유 입력, Endpoint/Key, 선택적 모델 목록 preview와 수동 ID, 최대 4모델 저장 제한, 시험 대상 수·사용료 가능성, Anthropic 첫 페이지 안내, 실제 서버 시험-후-저장 호출을 UI에 연결했다. 조회 실패 후 수동 ID와 Key 유지, 성공 뒤 Key 초기화, 비공용 일회성 `test_credential` 전달을 확인했다. preview는 기존 관리자 재인증 step-up의 `provider-connection:<id>` 대상과 grant를 재사용한다. 저장된 규격은 읽기 전용이고 legacy `CUSTOM`의 기존 catalog/Key 버튼을 유지한다. 일반 사용자의 개인 Key 및 허용 모델 읽기 전용 경계를 유지한다.
- BFF/API: same-origin `/bff/api/admin/provider-connections/model-preview` POST helper와 exact path/method allowlist만 추가했다. 다른 method/path 및 cross-origin 요청은 거부하고 query는 기존 BFF 계약처럼 upstream으로 전달하지 않는다. stale 테스트는 step-up이 존재해야 한다는 assertion으로 교정했다.
- 수정 파일: `apps/web/components/provider-settings-workspace.jsx`, `apps/web/lib/provider-settings-api.js`, `apps/web/lib/bff-api-proxy.js`, `apps/web/app/globals.css`(컴포넌트가 임베드된 화면에도 적용되는 scoped textarea/비용 고지 스타일), Node 테스트 3개, 이 진행 기록. Task 3 소스 수정 없음.
- 검증: scoped Node 3개 파일 `50 passed`; Web production build/TypeScript 통과, Web UI boundary `scannedFiles 475, violations 0, boundaryErrors 0`; 전체 `npm run verify:product-ui-boundary`는 최초 데스크톱 `dist` 누락으로 실패했으나 소스 변경 없이 데스크톱 정적 빌드 산출물 생성 후 `scannedFiles 498, violations 0, boundaryErrors 0`으로 통과. 대상 JS TypeScript 구문 검사 exit 0, API helper 단독 workspace lint PASS. `git diff --check` exit 0.
- lint 주의: `lint-workspace.mjs`를 모든 Task 4 JS/테스트에 그대로 적용하면 기존 컴포넌트의 `localhost` 보안 비교, 서버 BFF의 내부 URL, 테스트 fixture의 절대 URL/fetch를 금지 문자열로 잡아 exit 1이다. 이 규칙은 browser-source 전용이며 해당 입력은 검사 대상에 적합하지 않다. 해당 파일의 구문 검사와 product UI boundary는 통과했지만 전체 파일 scoped lint PASS라고 주장하지 않는다.
- 오류 횟수: 신규 제품 코드의 비의도 오류 0건. 최소 DOM fixture에서 텍스트 이벤트가 React 상태를 갱신하지 않아 테스트 전용 입력 헬퍼를 교정한 1건, 전체 boundary의 데스크톱 산출물 누락 1건, lint의 대상 불일치 1건을 각각 원인 확인·분리했다. 동일 근본 원인 3회 반복 없음.
- 미검증: 실제 브라우저 1920×1080·1440×900·430×844 클릭/Network, 실제 Provider·유료 시험, 실 DB/WSL/배포. Task 3의 기존 HTTP helper `NameError: user_id` 실패는 Task 4와 무관하며 재해결/재검증하지 않았다. 외부 Provider/실 DB/WSL 호출·원격 push·merge·deploy 0건.
- 다음 조치: Task 4 지정 파일만 scoped commit하고 clean 상태를 확인한 뒤 어울1에게 독립 리뷰를 요청한다. 브라우저/실 Provider/DB/WSL은 이번 Task 4 완료 증거로 주장하지 않는다.

## Task 4 독립 리뷰 Important 보완 — R1-PROVIDER-COMPAT-20261003-T4-REVIEW

- 일자/담당/시작점: 2026-10-03 / 어울2 단일 writer. 신산님이 Task 4 Important 2건 재작업을 지시했다. `codex/next-user-development`, HEAD `6830b434c3785c0541fffcce103f8457345d3381`, clean을 먼저 확인했다. 승인된 Task 4 UI/BFF와 대응 Node 테스트·이 기록만 수정한다.
- 원인 1: 명시 규격 CUSTOM에는 입력 Key를 쓰는 preview만 표시하고 저장된 공용 Key를 쓰는 기존 `refreshCatalog` 버튼을 숨겼다. RED 테스트에서 저장된 `CUSTOM/openai_compatible` 공용 연결의 `모델 조회` action 부재로 실패했다. GREEN은 저장된·공용·Key 설정된 명시 규격에만 기존 refresh action을 preview와 별도로 복원하고 legacy CUSTOM 동작은 유지했다. UI fixture는 refresh 후 수동 허용 ID 보존과 새 목록 모델의 별도 노출을 확인한다.
- 서버 계약 확인: `refresh_catalog()`는 공용 연결의 기존 암호화 Key를 `_prepare()`에 전달하고 CUSTOM이면 `_replace_models(..., mark_missing_stale=False)`를 사용하며 `_set_allowed_models()`를 호출하지 않는다. 기존 OpenAI/Anthropic 성공 및 조회 실패 fixture 3건 `3 passed, 30 deselected`; 성공 시 수동 검증 모델은 ready, 관리자 허용 목록은 그대로이고 목록에만 나온 모델은 자동 허용되지 않는다. 서버 소스·테스트 변경 없음.
- 원인 2: BFF static `model-preview` 분기가 모든 method를 먼저 포착해, 같은 문자열을 연결 ID로 쓴 named connection의 PUT/DELETE가 405였다. RED 테스트에서 PUT 405를 확인했다. GREEN은 POST만 preview static 경로로 예약하고 다른 method는 기존 SAFE_SEGMENT named connection 분기로 흘려 PUT/DELETE를 유지했다. 기존 preview POST·GET 405·same-origin 보호 회귀는 Node 묶음에서 통과했다.
- 검증: 신규 2건 각각 예상 원인 RED→GREEN; Task 4 Node 3개 파일 `52 passed, 0 failed`; 위 서버 계약 fixture `3 passed`; Web production build 및 내장 Web boundary PASS(`475 files, violations 0, boundaryErrors 0`), 전체 `npm run verify:product-ui-boundary` PASS(`498 files, violations 0, boundaryErrors 0`), 수정 JS TypeScript 구문 검사 exit 0, `git diff --check` exit 0. 이전 기록의 범용 workspace lint 대상 불일치/거짓 양성은 이번 보완에서도 미해결이며 전체 lint PASS로 주장하지 않는다.
- 변경 파일: `apps/web/components/provider-settings-workspace.jsx`, `apps/web/lib/bff-api-proxy.js`, `scripts/tests/provider-settings-web.test.mjs`, `scripts/tests/api-bff-runtime.test.mjs`, 이 진행 기록(5개)만. 비의도 구현 오류 0건, 의도한 RED 2건. 외부 Provider/실 DB/WSL/원격 push·merge·deploy 0건.
- 미검증/다음 조치: 실제 브라우저 클릭·Network/Provider·DB 지속 상태는 미검증이며 기존 Task 3 HTTP helper `NameError`는 범위 밖 그대로다. 5개 파일만 별도 scoped commit하고 clean 확인 후 어울1 독립 재검토를 요청한다.

## Task 5 Main agent 로컬 독립 검증 — R1-PROVIDER-COMPAT-20261003-T5-LOCAL

- 일자/담당/기준: 2026-10-03 / 어울1. `codex/next-user-development` HEAD `2ed35527`에서 Task 1~4 최종 로컬 범위를 확인했다. 단일 개발 writer의 마지막 보완 commit 후 worktree clean을 확인했고, 원격 push·PR·병합·배포는 수행하지 않았다.
- 독립 리뷰: Task 3에서 migration 0050의 기존 `CUSTOM` 규격값 차단과 Anthropic 형식 불량 답변 수용을 발견해 `f92455f2`에서 보완했다. Task 4에서 저장된 공용 CUSTOM의 모델 조회 버튼 소실과 `model-preview`라는 유효한 연결 ID의 PUT/DELETE 차단을 발견해 `2ed35527`에서 보완했다. 각각 후속 회귀 테스트를 추가했으며 보완 diff를 확인했다. 현재 남은 Critical/Important finding은 확인하지 못했다. 실제 Provider/DB/브라우저 수락을 의미하지 않는다.
- API 재검증: Provider·관리자·개인 Key·Workspace 모델·질문·문서·상태 점검 인접 10개 pytest 파일 `181 passed, 15 subtests passed`. 별도 `test_question_answering_runtime_http.py -q -x`는 기존 `test_identity_support.py:40`의 정의되지 않은 `user_id`로 첫 테스트가 실패(exit 1). 변경 전부터 같은 원인으로 12건이 실패했으며 이번 범위에서는 보조 파일을 수정하지 않았다. HTTP 런타임 gate는 미충족이다.
- Web 재검증: Node 화면/BFF/화면계약 3개 파일 `52 passed, 0 failed`; `npm run build --workspace @daon-user/web` PASS; `npm run verify:product-ui-boundary`는 498개 파일에서 위반 0건으로 PASS. `git diff --check 34f89b60..HEAD` PASS, worktree clean. URL/fetch fixture까지 브라우저 코드로 취급하는 범용 `lint-workspace.mjs`의 전체 대상 lint는 exit 1이므로 PASS라고 표시하지 않는다. API helper 단독 lint는 PASS이고 제품 UI 경계 검사는 PASS다.
- 변경·영향: 신규 `CUSTOM` 두 호환 규격의 관리자 조회/모델별 시험-후-저장, 비공용 일회성 시험 Key, 개인 Key 검증, Anthropic 질문 실행, 화면/BFF. 기존 연결·Key·허용 모델·Workspace 기본 모델을 재작성하는 migration은 추가하지 않았다. 외부 Provider 유료 호출 0회, 실 DB 데이터 변경 0건, WSL/Oracle 변경 0건. rollback 기준은 이 작업 이전 branch commit `34f89b60`; 각 Task commit은 Git에 보존한다.
- 오류 횟수/미검증: 독립 리뷰에서 확인한 새 회귀 위험은 Task 3 두 건과 Task 4 두 건으로 각각 수정·검증했으며 동일 근본 원인 3회 반복은 없었다. 기존 HTTP helper 오류 1원인, 범용 lint 대상 불일치 1원인은 미해결. 실제 1920×1080·1440×900·430×844 브라우저 클릭/Network, 실제 Provider 응답·비용, 실 PostgreSQL 데이터 전후, WSL URL은 미검증이다.
- 다음 조치/승인 경계: HTTP gate를 완전히 통과하려면 승인 범위 밖인 `test_identity_support.py:40` 보조 코드 수정 승인이 필요하다. WSL 개발 배포는 별도 승인과 read-only preflight 이후 exact SHA로만 수행한다. 그 전에는 push·PR·main 병합·WSL 배포를 하지 않는다.

## HTTP 테스트 helper 복구 — R1-PROVIDER-COMPAT-HTTP-HELPER-20261003

- 일자/담당/기준: 2026-10-03 / 어울2 단일 writer. `codex/next-user-development`, 시작 HEAD `75714f4d`, 시작 상태 clean. 신산님이 기존 HTTP helper `NameError`만 별도 승인했다. 운영 코드·DB·배포·원격 변경은 범위 밖이다.
- 원인/최소 수정: `test_identity_support.py`의 `identity_session_view(principal)`가 정의되지 않은 `user_id`를 `IdentitySessionView.login_id`에 넣었다. 실제 호출자는 `IdentityPrincipal`만 전달한다. fixture의 login ID를 `principal.user_id`로 바꾸고 해당 투영을 고정하는 테스트 1개를 같은 helper 파일에 추가했다. 운영 IdentitySessionView/호출자 계약은 변경하지 않았다.
- RED→GREEN: 변경 전 `test_question_answering_runtime_http.py -q -x` 첫 건과 신규 helper 집중 테스트가 모두 예상된 `NameError: user_id`로 RED. 수정 후 helper 집중 테스트 `1 passed`, 질문 HTTP `12 passed`(httpx 쿠키 deprecation warning 19건), Provider 설정 API HTTP 통합 `test_provider_settings_runtime_http.py` `12 passed`. 재검증 명령은 모두 로컬 Windows `.venv-win` Python과 `PYTHONPATH=services/api/src`를 사용했다.
- 제외/실패: 인접 파일 묶음 탐색에서는 `40 passed, 1 failed, 1 error`가 나왔다. `test_license_runtime_http.py`의 400 `LICENSE_DOCUMENT_INVALID`는 테스트 서명 문서가 `generation_runs` 리소스를 사용하지만 현재 라이선스 검증기의 허용 리소스가 `users`, `notebooks`뿐인 별도 fixture 불일치다. 격리 재현에서 `1 failed, 5 passed`; 이 라이선스 실패는 총 3회 관찰했다. 기본 pytest 임시 경로 `PermissionError` 1건은 전용 `--basetemp`로 분리했고, 1회 `PYTHONPATH` 누락 collection 오류를 바로잡았다. 어울1은 이 FAILURE_REPORT를 작업지시 실패보고 **1회**로 판정했고 라이선스 테스트는 이번 승인 범위에서 제외·재시도 금지했다. 라이선스 소스/테스트는 수정하지 않았다.
- 임시자원: 이 작업만의 `.pytest-tmp-http-helper-01372`를 worktree 내부에서 사용했다. 절대경로·비-reparse 디렉터리·내부 빈 폴더/내부 대상 링크만 확인한 뒤 정확히 이 디렉터리만 제거했고 잔류 없음.
- 변경 파일: `services/api/tests/test_identity_support.py`, 이 진행 기록만. 동일 helper 근본 원인의 반복 실패 0건(RED 후 수정); 별도 라이선스 실패는 helper 성공 판정에 포함하지 않는다.
- 미검증/다음: 전체 API suite, 실 Provider·DB, WSL·브라우저·배포는 이 helper 수정으로 검증하지 않았다. `git diff --check` exit 0; 지정 파일만 scoped commit한 뒤 어울1이 별도 라이선스 fixture와 WSL gate를 판단한다. push·PR·main 병합·배포는 하지 않는다.

## Task 5 WSL 개발 배포 및 검증 — R1-PROVIDER-COMPAT-20261003-T5-WSL

- 승인/담당: 2026-10-03 / 신산님이 대안 1(HTTP helper 수정 후 WSL 개발 배포)을 승인; 어울1이 로컬 독립 검증·WSL 배포를 수행했다. Oracle/ysna·main 병합·PR·실제 유료 Provider 시험은 범위 밖이다.
- 로컬 게이트: helper commit `e56a12e92458777598fe4565ff23806df493dbc7`를 독립 검토한 뒤 Provider/질문/HTTP/0050 인접 pytest 12개 파일 `199 passed, 15 subtests passed` 및 Node UI/BFF 3개 파일 `52 passed`를 재실행했다. Web production build와 TypeScript, Web boundary 475파일 0위반, 전체 UI boundary 498파일 0위반, API helper JS lint, `git show --check` 모두 통과했다. 범용 workspace lint의 기존 검사대상 불일치와 라이선스 문서 fixture `LICENSE_DOCUMENT_INVALID`는 해결·PASS 선언하지 않는다.
- 원격/checkout: Windows Git의 SSH alias가 없어서 WSL SSH를 통해 작업 브랜치에 비강제 push했고, 원격 ref가 위 전체 SHA임을 확인했다. WSL `SINSAN`의 IP `172.27.253.53`에서 새 격리 checkout `/home/daon/deploy/daon-user-provider-e56a12e9`를 GitHub에서 clone·detached checkout했으며 정확한 SHA와 clean 상태를 확인했다. 기존 `/home/daon/deploy/daon-user`의 다수 dirty 파일·untracked `secrets/`, 이전 실행 checkout `/home/daon/deploy/daon-user-license-0cccc639`의 `.env`/`secrets/`는 수정·삭제하지 않았다.
- 배포 전 데이터: 개발 DB `daon_user`의 Alembic `0050`; 연결 8, 카탈로그 모델 45, 관리자 허용 모델 45, 개인 Key 0, Workspace 기본값 2. 기존 API/Web/worker 4개 이미지 ID를 `rollback-provider-494518b4` 고유 태그로 보존했다. 전용 DB `pg_dump -Fc`, 객체 저장소와 API 런타임 볼륨 read-only tar를 `/home/daon/deploy/daon-user-qa-backups/2026-10-03-provider-compatible-e56a12e9`에 생성했다. DB dump `pg_restore -l`과 두 tar 전체 목록 읽기, 체크섬을 확인했다. DB dump 992503바이트, object archive 약 30MiB, runtime archive 약 8KiB. 비밀값·데이터 본문은 출력하지 않았다.
- 배포: 새 checkout의 Compose 2파일과 기존 환경 파일/Secret 경로를 사용한 `config --quiet`가 통과했다. 이미지 4개를 새 SHA에서 빌드하고, 기존 object-storage·PostgreSQL 컨테이너는 유지하면서 API→document/studio worker→Web만 순차 교체했다. 새 API·Web은 Docker `healthy`, 두 worker는 `running`; `http://172.27.253.53:3330/`은 HTTP 200이다. 새 컨테이너 4개의 Compose working directory가 새 checkout을 가리킨다.
- 배포 후 데이터: Alembic `0050`, 위 8/45/45/0/2 행 수가 모두 배포 전과 같다. 인증 없는 session/관리자 Provider/개인 Provider BFF 요청은 모두 예상대로 401이다. 실제 Provider Key 시험·외부 과금 호출 0회. 새 Web 빌드의 Node 22/요구 Node 24 engine warning은 기존 Dockerfile 그대로다. 별도 `npm audit --omit=dev --json`에서 critical 1건(`next` 직접 의존성), high 22건을 확인했다. 이전 배포 SHA 대비 `package-lock.json`과 Web Dockerfile은 동일해 이번 변경이 새로 도입한 것으로 판정하지 않지만, 안전하다는 뜻은 아니다. QA 배포에서는 의존성·이미지를 임의 변경하지 않았고 `main` 병합 전에는 별도 보안 판단이 필요하다.
- 기존 설정 관찰: 현재 WSL DB의 UPSTAGE 관리자 허용 목록에는 `solar-pro4` 외에도 다른 Solar/Syn 모델 14개가 있다(총 15개). 이번 배포는 해당 행을 변경하지 않았다. 신산님의 이전 `solar-pro4` 단독 사용 의도와 다를 수 있으므로 로그인 후 화면에서 확인하되, 승인 없는 운영형 설정/데이터 수정은 하지 않는다.
- 브라우저: Chrome에서 3330 새 화면을 열었으나 기존 인증 세션이 없어 로그인 화면으로 이동했다. 관리자·일반 사용자 실제 클릭, 세 viewport(1920×1080/1440×900/430×844), Network same-origin은 로그인 후 이어서 확인할 항목이며 현재 PASS가 아니다. WSL DB에 active 계정 2명과 미만료 access session 행은 있으나 현재 브라우저에 유효한 로그인 상태가 보이지 않았다. QA 비밀번호를 조회·출력·재설정하지 않고 신산님에게 직접 로그인 요청했다.
- rollback: 이전 checkout `494518b451febd15db5b8b7b6d89a57a42b4d274`, 보존 이미지 `*:rollback-provider-494518b4`, 기존 환경 파일과 세 백업이 복구 기준이다. 서비스·데이터를 자동 rollback하지 않았고 백업/기존 checkout도 삭제하지 않았다. `main` 병합·Oracle 배포는 수행하지 않는다.
- 상태/다음: WSL 배포와 무인증 smoke는 통과, 인수용 로그인 화면 검증은 대기. 현재 확인된 배포 증거를 먼저 같은 작업 브랜치에 scoped commit/push하고, 신산님 로그인 후 관리자/일반 사용자 브라우저 검증 결과를 후속 기록한다. 별도 라이선스 fixture와 audit warning은 후속 판단 대상으로 남긴다.

## Task 5 로그인 후 관리자 브라우저 확인 — R1-PROVIDER-COMPAT-20261003-T5-BROWSER

- 일자/담당/대상: 2026-10-03 / 어울1. 신산님이 직접 로그인 완료한 기존 Chrome 세션에서 `http://172.27.253.53:3330/`의 관리자 계정 화면을 읽기 전용으로 확인했다. 브라우저에 `admin · 시스템 관리자`가 표시되고 `/notebooks`에서 `/settings/model-connections`로 이동했다.
- 관리자 화면: 시스템 연결·모델 목록 조회 성공 메시지, 기존 연결 8개, 사용 후보 4개를 확인했다. 새 연결의 `호환 방식`에 `기존 Provider 방식`, `OpenAI 호환`, `Anthropic 호환`이 표시된다. OpenAI 호환 선택 시 `Chat Completions`, Anthropic 호환 선택 시 `Messages`가 표시되고 Provider 표시 이름·수동 모델 ID 입력·최대 4개 허용 안내가 나온다. Anthropic 화면에는 모델 목록 첫 페이지만 표시될 수 있다는 안내가 있다. 입력·시험·저장은 하지 않았다.
- 기존 설정: UPSTAGE 연결은 `공용 · 관리자 Key 필요 · 사용 가능`으로 표시된다. 허용 모델 체크박스 15개가 모두 선택되어 있으며 `solar-pro4`도 포함된다. 신산님의 `solar-pro4` 단독 사용 의도와 현재 설정이 다르다. 기존 DB 설정, Key, 모델 허용 목록을 변경하지 않았다.
- 화면 크기: 브라우저 viewport override 1920×1080, 1440×900, 430×844에서 관리자 설정 화면의 로딩·연결 목록·선택 상세 패널 표시를 확인하고 override를 해제했다. 모바일 폭에서는 상세 패널이 목록 아래로 배치된다. 육안상 관리자 섹션 제목·모델 카탈로그 일부 텍스트의 명암 대비가 낮아 가독성 개선 여지가 있다. 이를 화면 인수 PASS로 과장하지 않는다.
- 브라우저 오류/경로: 이 세션에서 수집된 console warn/error는 0건이고, 현재 화면의 확인 가능한 링크는 상대 경로 `/notebooks`였다. 브라우저 Network의 실제 fetch URL을 직접 관찰할 수 없어 same-origin 실제 요청 검증은 미완료이며, 로컬 Web boundary 테스트의 PASS와 구분한다.
- 경계/미검증: 일반 사용자 계정의 라이브 화면·개인 Key 경로는 별도 로그인 세션이 없어 미검증이다. 유료 Provider 호출·모델 조회·관리자 설정 저장·개인 Key 저장·DB 변경은 0건. 이 확인은 로그인된 관리자 화면의 읽기 전용 QA이며 실제 OpenAI/Anthropic Provider 연동 성공을 뜻하지 않는다.
- 판정/다음: 배포된 신규 호환 방식의 관리자 UI 표시는 확인. 일반 사용자 라이브 인수, 실제 Network, `solar-pro4` 단독 허용 설정은 완료로 판정하지 않는다. 후자는 신산님이 설정 변경을 명시적으로 지시할 때만 적용한다. 기존 라이선스 fixture 실패와 의존성 audit finding도 그대로 남긴다. 이 기록만 작업 브랜치에 commit/push하고 제품 SHA `e56a12e9`·WSL 실행 상태·main은 변경하지 않는다.

## 신산님 화면 테스트 후속 수정 — 내부 ID 비노출·Provider 관리자 재인증 제거

- 일자/담당/범위: 2026-10-03 / 어울. 신산님이 새 연결의 자동 생성 `Connection ID`를 화면에서 숨기고, 로그인된 시스템 관리자에게 Provider 연결 관리 시 별도 비밀번호 재입력을 요구하지 않도록 직접 지시했다. 대상은 기존 `codex/next-user-development` 격리 worktree와 WSL QA URL `http://172.27.253.53:3330/`; Oracle·ysna·main은 제외한다.
- 원인/변경: 새 연결 ID가 수동 입력란에 노출되고, Provider UI의 비밀번호 상태·step-up 호출과 API 본문 필수 필드·grant 소비가 저장/조회/삭제를 함께 가로막았다. 새 연결 ID는 내부에서 생성하되 UI에서 보이지 않게 했고, Provider 관리자 API는 기존 시스템 관리자 세션/역할 검사와 version/idempotency/삭제 확인을 유지하면서 별도 step-up을 요구하지 않게 했다. 다른 기능의 step-up은 변경하지 않았다. 설계/계획의 해당 계약도 최신 직접 지시로 정정했다.
- 변경 파일: `provider-settings-workspace.jsx`, `provider-settings-api.js`, `runtime.py`, 해당 Node/API 테스트 3개와 설계/계획 문서 2개 및 이 기록. 기존 DB schema, 사용자 Key 정책, Secret 값은 변경하지 않았다.
- RED→GREEN: 관리자 화면의 비밀번호 없는 저장 테스트는 재인증 필드 존재로 RED 후 GREEN; API 비밀번호 없는 모델 preview는 `INVALID_REQUEST` RED 후 GREEN. 로컬 Provider 관련 API 집중 6개 파일 `128 passed`, Provider 화면/BFF Node 3개 파일 `53 passed`, Web production build/TypeScript 및 Web boundary 475파일 0위반, 전체 UI boundary 498파일 0위반, `git diff --check` 통과. 비의도 오류 0건; 기존 재인증을 기대하던 회귀 테스트는 최신 정책으로 교정했다.
- WSL 배포 전 확인: 현재 `SINSAN` WSL 인스턴스 주소는 `172.27.253.53`, 실행 checkout은 `e56a12e92458777598fe4565ff23806df493dbc7`에서 clean, API/Web healthy. SSH alias 연결은 host-key 검증에 실패해 우회하지 않았으며 동일 WSL 인스턴스에서 직접 확인했다. 신규 배포는 검증된 exact commit으로 별도 Git checkout에 구성하고 기존 checkout·Secret·DB를 보존한다. 배포 전/후 이미지와 DB 상태 확인 및 실패 시 이전 이미지 복구를 기록한다.
- 현재 미검증/다음: WSL 새 commit 배포·실제 브라우저 로그인 화면·same-origin Network·실 Provider 유료 호출은 아직 확인되지 않았다. 변경 파일만 scoped commit·branch push → WSL exact-SHA checkout/빌드/교체 → URL 화면 및 비파괴 smoke → 사용자 확인 순서로 진행한다.

## 신산님 화면 테스트 후속 수정 WSL QA 배포 결과

- 일자/담당/범위: 2026-10-03 / 어울. 신산님이 직접 지정한 WSL QA URL `http://172.27.253.53:3330/`에 위 후속 수정을 배포했다. Oracle·ysna·main은 변경하지 않았다.
- Git/배포 기준: 변경 9개 파일을 `89de2e957eaf38585ad26108f7d8e41a8fc7ad2a`로 commit하고 `origin/codex/next-user-development`에 비강제 push했다. WSL의 별도 checkout `/home/daon/deploy/daon-user-provider-89de2e95`를 동일 exact SHA로 만들었고 기존 checkout은 유지했다.
- 배포 조치: 기존 API/Web 이미지를 각각 `rollback-provider-89de2e95-pre` 태그로 보존하고, 기존 환경 파일과 Secret 경로를 변경하지 않은 채 새 checkout에서 API/Web 이미지만 빌드·교체했다. PostgreSQL·객체 저장소·document/studio worker는 재생성하지 않았다.
- 배포 후 확인: 새 API/Web 컨테이너가 healthy이고 두 worker는 계속 running이다. `/`와 `/settings/model-connections`는 HTTP 200, 비인증 `/bff/api/session`은 예상대로 401이었다. 로그인된 Chrome의 새 탭에서 기존 연결 8개와 사용 후보 4개가 로드되고 자동 생성 `Connection ID` 및 `관리자 재인증 비밀번호` 입력란이 표시되지 않음을 확인했다. 이 확인은 화면 조회이며 설정 저장·삭제 클릭은 하지 않았다.
- 데이터 불변 확인: 개발 DB의 `system_provider_connections.provider_code` 읽기 전용 조회 결과는 EOUL_GATEWAY, GROQ, MEDIA_BRIDGE, MISTRAL, OLLAMA, OMNIROUTE, OPENROUTER, UPSTAGE의 8개다. ANTHROPIC·OPENAI·GEMINI 연결은 현재 없으며 이번 작업에서 재등록하거나 다른 연결 데이터를 변경하지 않았다.
- 오류/미검증: 읽기 전용 DB 확인 때 잘못 추정한 테이블명 `provider_connections` 조회 1건이 실패해 스키마를 확인한 뒤 실제 `system_provider_connections`를 조회했다. WSL 무권한 셸 호출 1건은 `E_ACCESSDENIED`였고 승인된 실행 경로로 재조회했다. 외부 Provider 실제 호출, 유료 시험, 저장/삭제 클릭, 브라우저 Network의 실제 fetch URL, 일반 사용자 화면, 전체 API suite는 미검증이다. 동일 근본 원인 3회 반복 없음.
- 다음: 신산님이 위 QA URL에서 새로고침 후 화면을 직접 확인한다. 기존에 없는 세 Provider 연결과 UPSTAGE 허용 모델 설정은 별도 지시 없이 변경하지 않는다. `main` 병합·Oracle 배포는 인수 전 진행하지 않는다.

## Media Bridge 모델 조회 복구 — 2026-10-04

- 담당/기준: 어울, 기존 `codex/next-user-development` 단일 writer. 시작 HEAD `ce5d5553`, clean. 신산님이 Media Bridge 모델 조회 예외를 제거하고 WSL QA URL `http://172.27.253.53:3330/`에서 직접 확인할 수 있게 하라고 지시했다.
- 원인: Web이 `MEDIA_BRIDGE`를 모델 조회 버튼 제외 집합에 넣고, API `MediaBridgeAdapter.discover_models()`가 모델 API를 호출하지 않고 빈 목록을 반환했다. 반면 같은 Adapter의 연결 확인은 `/v1/models`를 이미 호출하고 있었다.
- 수정 파일: `apps/web/components/provider-settings-workspace.jsx`, `services/api/src/daon_user_api/provider_connection_adapters.py`, 각 대응 회귀 테스트 `scripts/tests/provider-settings-web.test.mjs`, `services/api/tests/test_provider_connection_adapters.py`, 이 진행 기록. 기존 연결·Key·허용 목록은 코드 변경으로 재작성하지 않는다.
- RED→GREEN: 신규 Python 테스트는 모델 목록이 빈 값으로 나와 실패, 신규 Web 테스트는 Media Bridge 조회 버튼 부재로 실패한 뒤 각각 통과. Adapter 대상 `63 passed`, 관리자·카탈로그 포함 API 인접 `152 passed`, Web 화면/계약 `25 passed`, Web production build·TypeScript·경계 검사 475파일 0위반.
- 전체 API suite 관찰: 기본 pytest 임시 디렉터리 접근 거부를 전용 `--basetemp`로 분리한 후 `255 passed, 14 skipped, 1 failed, 171 subtests passed`에서 중단. 실패는 기존 라이선스 fixture의 `LICENSE_DOCUMENT_INVALID`이며 이번 Provider 변경 파일이 아니다. 전용 임시 디렉터리는 경계 확인 후 제거했다.
- WSL 읽기 전용 확인: 현재 API/Web은 clean한 `827c9189` checkout에서 실행 중. 설치형 Media Bridge는 WSL 호스트의 `127.0.0.1:8642/v1/models`에 HTTP 200, Daon API 컨테이너에서 같은 loopback 주소는 연결 거부. 기존 Docker host gateway `172.17.0.1:8642/v1/models`는 API 컨테이너에서 HTTP 200. 배포형 HTTPS `/v1/models`는 컨테이너에서 인증 없는 요청에 401. Secret 원문 출력·유료 Provider 호출 0회.
- 다음 조치: 지정 파일만 commit/push 후 WSL에서 exact SHA checkout으로 API/Web을 빌드·교체, healthy·URL·화면을 확인한다. QA Media Bridge 연결의 loopback Endpoint는 조회 가능한 Docker host gateway로 관리자 화면을 통해 검증 후 수정할 필요가 있다. Oracle/ysna/main 및 다른 연결 설정은 범위 밖이다.

## Media Bridge 모델 조회 WSL QA 반영 — 2026-10-04

- 담당/배포 기준: 어울. 수정 5개 파일을 `411e0d071554a6a4ce5121f87a57e80e2bafa733`로 commit하고 작업 브랜치에 비강제 push했다. WSL의 별도 clean checkout `/home/daon/deploy/daon-user-media-411e0d07`가 동일 SHA인 것을 확인한 뒤 API/Web 이미지 2개만 빌드·교체했다. 이전 API/Web 이미지는 각각 `rollback-media-411e0d07-pre` 태그로 보존했다. 다른 worker·PostgreSQL·Secret 파일·Oracle·ysna·main은 변경하지 않았다.
- 실행 확인: `daon_user-api-1`과 `daon_user-web-1`이 healthy, `/settings/model-connections` HTTP 200이다. 실행 중인 두 컨테이너의 Compose checkout은 새 exact-SHA checkout이다. Media Bridge `/v1/models`는 API 컨테이너에서 `host.docker.internal:8642`로 HTTP 200이며 새 Adapter가 `solar-pro4` 한 개를 실제 조회했다.
- QA 연결 조정: 개발 DB의 정확한 `provider-media_bridge` 1개 행이 `http://127.0.0.1:8642/v1`, version 7, failed인 것을 읽기 전용 확인했다. API 컨테이너의 127.0.0.1은 다른 컨테이너 자신을 가리켜 연결이 거부되므로, 대상 행·기존 값·버전을 조건으로 `http://host.docker.internal:8642/v1`로 조정하고 version 8, unverified로 되돌렸다. Key·공용 정책·허용 모델은 변경하지 않았다. 저장된 QA 행을 새 Adapter로 다시 조회해 `solar-pro4` 1개를 확인했다. 원래 URL과 version 7은 이 기록에 남겼다.
- 화면/미검증: 기존 Chrome 관리자 탭 새로고침 후 로그인 세션이 만료되어 로그인 화면으로 이동했다. 따라서 배포 화면의 `모델 조회` 버튼 클릭, same-origin Network와 목록 반영, 사용자 수락은 아직 검증하지 못했다. 신산님에게 기존 QA 관리자 세션 재로그인을 요청했다. 비밀번호·토큰은 출력하거나 저장하지 않았다. 전체 API suite는 위 기존 라이선스 fixture 실패로 여전히 GREEN 아님.
- 다음 조치: 로그인 후 `/settings/model-connections`에서 `MEDIA_BRIDGE` 선택, `모델 조회` 클릭, `solar-pro4`의 카탈로그 표시와 Network 경로를 확인한다. 그 전에는 화면 기능까지 검증 완료로 보고하지 않는다.

## 신규 연결 기존 Provider 목록 제거·Key 선택 — 2026-10-04

- 담당/기준: 어울. 기존 `codex/next-user-development` 단일 writer, 시작 HEAD `b9f4bd3a`, 기존 worktree clean. 신산님은 신규 등록의 `기존 Provider 방식` 및 Provider 목록 제거와 `OpenAI 호환`/`Anthropic 호환`만 표시, API Key 필요 여부 선택을 직접 지시했다. 설치형 `MEDIA_BRIDGE` 연결은 신산님이 이미 삭제했으며 배포형 `Media Bridge Server`와 저장된 Key는 수정하지 않는다.
- 원인/조치: 신규 draft가 `OLLAMA` 및 Key 불필요로 시작해 기존 Provider 드롭다운을 노출했다. 호환 draft를 기본값으로 만들고 신규 등록의 기존 Provider 선택을 제거했다. 호환 연결의 Key 불필요 선택은 기존 서버 정책이 거절했으므로 `CUSTOM` 공용 연결에서만 Key 불필요를 허용하고, 모델 조회·모델별 시험·실제 질문에 인증 헤더를 붙이지 않도록 했다. 기존 저장 연결의 조회/수정 경로는 유지한다. 설계·계획 문서에 최신 직접 지시를 반영했다.
- 변경 파일: Web 관리자 연결 화면 및 Node 테스트, Provider 관리자 정책/Adapter/HTTP body, 모델 resolver/질문 실행 및 Python 테스트, 호환 설계/계획과 이 진행 기록. DB migration·기존 연결 행·Secret·운영 데이터는 변경하지 않는다.
- 검증: 신규 UI 테스트 RED→GREEN. Web/계약 Node 3파일 `26 passed`; Web production build와 TypeScript PASS; 전체 UI 경계 498파일, Web 경계 475파일 위반 0. Python 신규 Key 불필요 저장/조회/resolver/실행 테스트 `4 passed, 2 subtests passed`; Provider/질문 인접 5파일 `189 passed, 20 subtests passed`; API HTTP/개인 Key 인접 3파일 `39 passed`. 기존 신규 OmniRoute 등록 테스트 3개는 제거된 등록 경로를 전제하므로 삭제했으며 저장된 OmniRoute 관리 회귀 테스트는 유지했다.
- 오류 횟수/미검증/다음: Python 실행기 경로 탐색 중 Windows `python`/`py` 사용 불가, WSL 시스템 Python의 `argon2` 누락을 확인한 뒤 기존 Daon_User WSL venv로 검증했다. Key 불필요 질문 신규 테스트 첫 실행은 `prepare_general`에 남은 필수 Key 호출 1건으로 실패해 수정·재실행했다. 동일 근본 원인 3회 연속 없음. 사용자 실제 화면 클릭·저장과 실제 Key 불필요 Provider 연동은 신산님 확인 전 미검증이다. 다음은 변경 파일만 commit/push, WSL exact-SHA API/Web 배포, URL/health/DB 불변 smoke 후 신산님이 3330에서 확인한다. Oracle·ysna·main·기존 Key는 범위 밖이다.

## 신규 호환 등록·Key 선택 WSL QA 배포 — 2026-10-04

- 제품 기준: `7db19f1fdebf78478b2757fa268fe9cd9071e33d`를 `origin/codex/next-user-development`에 비강제 push. WSL에서 GitHub로 별도 clean detached checkout `/home/daon/deploy/daon-user-compat-7db19f1f`를 생성해 같은 SHA를 확인했다. 기존 실행 checkout·환경 파일·Secret은 변경하지 않았다.
- 배포 전: API/Web healthy, 실행 checkout은 `411e0d07`이었다. DB 연결 11개, `Media Bridge Server`는 `CUSTOM/openai_compatible`로 Key 저장 상태, 설치형 `MEDIA_BRIDGE` 0개. API/Web의 이전 image를 `rollback-compat-7db19f1f-pre` 태그로 보존했다.
- 배포: 새 checkout과 기존 환경·Secret 참조로 Compose config PASS, API/Web 이미지만 빌드·순차 교체했다. API image `sha256:de936f5978582d23d9a90223fda1cdf11b3cb9b401a45817367a0da30e76a4cd`, Web image `sha256:e20ace4949bf988b8cfa771965aeac6d85e38c917085e02e6a05c6075454e0ee`. 두 컨테이너의 Compose working directory가 새 checkout을 가리키고 healthy, 두 worker는 계속 running이다.
- 비파괴 smoke: `http://172.27.253.53:3330/settings/model-connections` HTTP 200, 비인증 BFF session 예상 401. DB 연결 11개·설치형 `MEDIA_BRIDGE` 0개 불변, Alembic `0050`. 실제 사용자 저장/삭제 클릭, Provider 유료 호출, 브라우저 Network는 수행하지 않았다.
- 오류/미검증/다음: DB 읽기 전용 사전 조회에서 잘못 추정한 `daon_user` role 1건과 SQL 인용 1건을 확인 후 실제 컨테이너 환경의 계정으로 조회했다. 제품/DB 수정으로 이어지지 않았다. 신산님이 3330에서 새 연결의 두 호환 방식, `Key 필요/불필요`, 실제 저장 결과를 확인한다. 사용자의 실사용 수락 및 외부 Keyless Provider 연동은 미검증이다. Oracle·ysna·main·기존 Key/연결 데이터는 변경하지 않았다.

## OmniRoute 모델 카탈로그 조회 오류 수정 — 2026-10-04

- 담당/기준: 어울, 기존 `codex/next-user-development` 단일 writer. 시작 HEAD `40fd5719`, clean. 신산님 화면에서 저장된 OmniRoute 연결의 `모델 조회` 후 카탈로그 저장 오류가 표시되고, OmniRoute 자체 화면에는 발견 모델 약 840개·등록 0개가 표시됐다.
- 원인: WSL QA API 컨테이너에서 저장된 Key를 출력하지 않고 읽기 전용 `/v1/models`를 호출해 HTTP 200, 839행 중 공백 포함 ID 94개를 확인했다. 기존 `ProviderCatalog.from_payload(..., "CUSTOM", ...)`는 유효하지 않은 ID 하나만 있어도 전체 목록을 거부해 `PROVIDER_CATALOG_RESPONSE_INVALID`가 발생했다. DB의 OmniRoute 연결·Key·허용 목록은 수정하지 않았다.
- 조치: OmniRoute 목록에 한해 기존 저장용 모델 ID 규칙을 통과하지 않는 행을 제외하고 유효한 ID만 기존 카탈로그 검증에 전달한다. 응답 구조·민감 키 검사와 credential 포함 ID 거부, 실제 모델 사용 전 probe, 다른 Provider의 엄격한 검증은 유지한다. 새 schema·권한·연결 정책 변경은 없다.
- RED→GREEN: 공백 ID와 정상 ID가 섞인 응답의 조회 테스트가 수정 전 `PROVIDER_CATALOG_RESPONSE_INVALID`로 실패했고 수정 후 통과했다. Adapter·Catalog `81 passed`, 관리자 저장/HTTP 인접 `88 passed`, `git diff --check` 통과. 전체 API suite는 기존 라이선스 fixture의 `LICENSE_DOCUMENT_INVALID`에서 `255 passed, 14 skipped, 1 failed, 171 subtests passed`로 중단되어 전체 GREEN이 아니다.
- 변경 파일: `provider_catalog.py`, `provider_connection_adapters.py`, `test_provider_connection_adapters.py`, 이 진행 기록. 진단 과정의 잘못된 SQL 인용 1건·Adapter 생성자 인수 누락 1건은 읽기 전용 진단 실패였고 제품/DB 변경은 없었다. 동일 원인 수정 3회 반복은 없다.
- 미검증/다음: 아직 3330 배포 및 신산님 실제 클릭은 미검증. 변경 파일만 안전한 commit/branch push 후 WSL exact-SHA API 이미지만 교체하고 읽기 전용 실제 카탈로그 조회·health·URL·DB 불변을 확인한다. Web·worker·Oracle·ysna·main·기존 Key는 변경하지 않는다.

### OmniRoute 조회 오류 WSL QA 반영

- 제품 commit `4e96366c454208d8af89c93bf73dc9338a504990`를 기존 작업 브랜치에 비강제 push하고, WSL 전용 clean checkout `/home/daon/deploy/daon-user-omnicatalog-4e96366c`에서 exact SHA를 확인했다. 기존 Compose 환경/Secret 참조를 유지하고 API 이미지만 빌드·교체했다. 이전 API image는 `daon_user-api:rollback-omnicatalog-4e96366c-pre`로 보존했다.
- 새 API image `sha256:234832be6619e160014a668ffa75fd71b11b785a33010c31f44f1e800d7da9ed3`가 위 checkout에서 실행되며 healthy. Web은 기존 `7db19f1f` 이미지로 healthy이고 두 worker는 계속 실행 중이다. `http://172.27.253.53:3330/settings/model-connections` HTTP 200.
- 새 API 컨테이너에서 저장된 OmniRoute Key를 출력하지 않는 읽기 전용 조회 결과: `catalog_status=ok`, 텍스트 후보 682개, 별도 기능 유형 63개. 상위 응답 839행 중 기존 모델 ID 규칙에서 제외되는 94개가 있었으며 이를 저장/등록하지 않았다. 실제 관리자 `모델 조회` 클릭과 카탈로그 DB 저장은 수행하지 않았다.
- 배포 전후 DB 연결/카탈로그/허용 모델 수는 `11/47/33`으로 불변, OmniRoute 연결 version 7·Key 저장 상태 유지. Oracle·ysna·main·Web/worker·기존 Key는 변경하지 않았다. 사용자 수락과 화면에서 실제 조회·등록은 신산님 확인 전 미검증. 다음: 신산님이 3330에서 새로고침 후 OmniRoute `모델 조회`를 클릭해 오류가 사라지고 목록이 반영되는지 확인한다.

## Provider 설정 1920×1080 스크롤 정리 — 2026-10-04

- 담당/상태: 어울, 기존 `codex/next-user-development` 단일 writer. 화면 변경 제품 commit `3cfd462efe4afa0e8523ecf1e856a8344ec66c75`를 비강제 push했고, WSL clean detached checkout `/home/daon/deploy/daon-user-layout-3cfd462e`에서 동일 SHA를 확인했다.
- 변경: `apps/web/app/globals.css`의 모델 체크 목록을 최대 320px·내부 세로 스크롤로 제한. `apps/web/app/settings/model-connections/provider-settings.css`에서 상단 상태/제목 간격을 줄이고 제목 액션을 같은 행에 배치했으며, 왼쪽 연결 목록과 오른쪽 상세 패널을 뷰포트 기준 최대 760px·각각 내부 스크롤로 제한했다. 카드 여백/줄간격도 축소했다. 연결·모델·Key 저장 로직은 변경하지 않았다.
- RED→GREEN: 신규 실제 Edge 배치 회귀 검사 `scripts/tests/provider-model-picker-layout.test.mjs`는 수정 전 모델 목록 5371px, 연결 목록 9564px로 실패했고 수정 후 2 passed. Provider Web/계약 23 passed, Web production build·TypeScript·경계 검사 475파일 0위반, `git diff --check` 통과. 브라우저 회귀 검사는 Edge가 없는 환경에서는 skip되므로 WSL 브라우저 실측은 수행하지 않았다.
- QA 배포: 이전 Web image `sha256:e20ace4949bf988b8cfa771965aeac6d85e38c917085e02e6a05c6075454e0ee`를 `daon_user-web:rollback-layout-3cfd462e-pre`로 보존한 후 Web만 새 image `sha256:3dc918745ba8867270c33614c2cf2c1045176549fb809442298df3f18bae3484`로 교체했다. 새 Web Compose checkout·healthy, API 기존 image `sha256:234832be6619e160014a668ffa75fd71b11b785a33010c31f44f1e800d7da9ed3` healthy, 두 worker 계속 running. WSL·Windows 양쪽에서 `http://172.27.253.53:3330/settings/model-connections` HTTP 200. DB·Secret·Provider 연결·Key·Oracle·ysna·main은 변경하지 않았다.
- 확인 주기 `0` 요청: 현재 UI는 60~1440분만 표시하고 API는 `ge=1`, 서비스는 최솟값 1, DB `CHECK (interval_minutes BETWEEN 1 AND 1440)`이며 반복 루프는 0을 받으면 즉시 재실행한다. 실제 중지 기능은 DB 제약 변경과 실행 루프 수정이 필요해 별도 승인 답변을 요청했다. 이 배포에는 0분 기능을 포함하지 않았다.
- 미검증/다음: 신산님 실제 관리자 화면 스크롤·저장 조작과 사용자 인수는 미검증. 신산님이 3330에서 모델 목록·연결 목록·상세 패널 내부 스크롤과 상단 간격을 확인한다. 확인 주기 0의 별도 승인 시 API/DB 변경을 독립 검증·배포한다. 동일 근본 원인 3회 반복 없음.

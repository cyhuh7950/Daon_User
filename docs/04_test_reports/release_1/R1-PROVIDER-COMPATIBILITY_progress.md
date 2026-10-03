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

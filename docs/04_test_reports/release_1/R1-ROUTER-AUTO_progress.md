# 라우터 Auto 모델 작업현황

- 담당: main agent 어울. 기준 브랜치 `codex/next-user-development`, 구현 시작 `c17c7e77`.
- 승인 범위: OmniRoute=`auto`, OpenRouter=`openrouter/auto`, 관리자가 지정한 OpenAI 호환 CUSTOM의 실제 Auto ID. Combo 특수 처리 없음. 모델 미지정의 기준 모델 동작은 Auto 명시 선택과 별개. 기존 Media Bridge Server 설정 변경은 제외. 후속 직접 지시로 병합·배포 및 License 테스트 보정 범위가 추가 승인됨.

## 단계별 진행 (2026-10-04)

1. 스키마·연결 계약: `b81dc617` 및 후속 보완. `0052_router_auto_model.py`, 연결 관리자·Runtime 및 회귀 테스트. 관련 테스트 102 passed. 오류: 최초 예상 RED 3건; 테스트 실행 환경의 `PYTHONPATH` 누락 1건을 수정 후 재검증. 기존 OmniRoute `auto` 카탈로그 행만 `logical`로 표시하도록 backfill; 기존 연결의 키·허용 목록·기본 모델은 migration에서 재작성하지 않음.
2. 카탈로그·허용 목록: `376ec12c`. 조회 출처 `upstream`/`logical`을 분리하고 Auto 중복 방지, OmniRoute의 Auto·다른 모델 공존, CUSTOM Auto 시험 실패 시 불변성, 모델 미지정 probe의 `model` 생략을 구현. 관련 테스트 169 passed. 구현 중 신규 RED 및 기존 기대값 변경을 분리해 수정; 최종 관련 테스트 실패 0.
3. 관리자 화면: `ef65fb19` 및 검토 보완. CUSTOM/OpenAI 호환 라우터 Auto 설정, 고정 라우터 ID 읽기 전용 표시, 논리 모델 표기, 실제 ID 선택, 기존 내부 스크롤 유지. 기존 5개 허용 모델을 둔 OpenRouter 연결의 무관한 저장 회귀와 개인 CUSTOM의 Auto 변경 시 일회성 시험 Key 안내를 보완. Node/BFF 화면 테스트 70 passed, Web build PASS, UI 경계 검사 PASS. 최종 관련 테스트 실패 0.
4. 실행 ID·통합: 진행 중. 질문 fixture에서 OmniRoute `auto`→Responses, OpenRouter `openrouter/auto`→Chat Completions, OpenAI 호환 Media Bridge `auto`→Chat Completions의 실제 `model` 필드를 검증. 허용된 Auto의 Workspace 기본 모델 저장·해석과 기존 기준 모델을 함께 확인. 개인 OmniRoute의 허용 모델이 비어 있을 때 Key 시험에서 `model` 생략, 개인 CUSTOM의 Auto 추가 후 사용자 Key 검증 상태 해제, OpenRouter 조회 Auto의 생성 직후 upstream 출처 보존을 회귀 테스트로 고정. 전체 관련 집중 묶음은 252 passed 및 11 subtests passed. 실제 Provider 호출 없음.

## 전체 로컬 검증과 예외

- API 전체 테스트 1차: Windows 기본 pytest 임시 폴더 접근 거부로 43 setup errors. 코드 오류와 분리함.
- API 전체 테스트 최종 재실행: 작업 폴더의 새 pytest 임시 경로로 895 passed, 48 skipped, 10 failed, 207 subtests passed. 실패 10건은 모두 기존 License 테스트(`test_license.py` 9건, `test_license_runtime_http.py` 1건). `license.py`의 `_RESOURCE_CODES`는 `users`, `notebooks`만 허용하지만 기존 fixture는 `generation_runs`를 사용하여 `LICENSE_DOCUMENT_INVALID`가 발생. 이번 브랜치는 License 제품 코드·테스트를 변경하지 않음. 이 결함은 승인된 Auto 범위 밖이므로 수정하지 않음. 테스트용 임시 경로는 정확한 대상 확인 후 제거함.
- 읽기 전용 코드 검토: 중요 3건(미선택 개인 OmniRoute Auto probe, 개인 CUSTOM Key 재검증, OpenRouter 5개 허용 모델 화면 저장)과 경미 2건(OpenRouter 초기 출처, 개인 CUSTOM 시험 Key 안내)을 확인하여 각각 재현 테스트 RED→수정→관련 테스트 GREEN으로 보완함.
- 설치된 로컬 `psql`/PostgreSQL/Docker/Podman 명령이 없어 격리 PostgreSQL의 실제 migration 보존 검증은 실행하지 못함. migration SQL·테스트의 정적/fixture 검증은 통과했지만 실제 DB 적용 성공으로 판정하지 않음. WSL DB에는 변경하지 않음.
- 후속 License 보정(2026-10-04): 현재 제품 License 계약은 `users`·`notebooks` 수량과 `llm_access`·`notebook_management` 기능이다. 기존 테스트의 폐기된 `generation_runs`·`citation` 등 fixture/기대값만 갱신하고 제품 License 코드는 변경하지 않음. 만료 시 생성 차단·기존 읽기 허용, 서명·재요청·권한 검증을 보존. 보정 전 10 failed/12 passed, 보정 후 License 관련 23 passed.
- 전체 로컬 API 재검증: 906 passed, 48 skipped, 207 subtests passed. 관리자 UI 관련 Node 테스트 39 passed, Web production build 및 UI 경계 검사 PASS(475개 파일, 위반 0), `git diff --check` PASS. 작업 중 생성한 pytest 임시 폴더 3개는 정확한 경로 검증 후 제거함.
- 오류 횟수: 본 작업 구현 중 Auto 관련 지속 오류 0; License 계약 불일치 1회 발견·보정 완료. 로컬 코드 gate는 GREEN이나 실제 PostgreSQL migration과 WSL/Oracle 검증은 아직 NON-GREEN/UNVERIFIED.
- 미검증: 실제 PostgreSQL migration 및 기존 연결·Key·허용·기본값 보존, 실제 Media Bridge Server Auto 생성 응답, WSL/Oracle 브라우저·배포, 사용자 인수. 현재 브랜치를 `main`에 병합하거나 배포하지 않음.
- Rollback: 작업 브랜치의 체크포인트 `c17c7e77`이 제품 코드 변경 전 기준. 실제 DB migration 미적용 상태이므로 DB rollback 수행 없음. 기존 연결/Key/허용/Workspace 기본값 변경 없음.
- 다음 조치: 격리 PostgreSQL에 `0052` migration을 적용하여 연결·Key·허용 목록·Workspace 기본값 보존을 검증한다. 이후 안전한 commit·push, 지정 WSL 정식 QA, PR, Oracle staging 사용자 인수, 승인 후 병합·재배포 순으로 진행한다. 각 환경에서 실행하지 못한 항목은 PASS로 승격하지 않는다.

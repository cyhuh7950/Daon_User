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
- 배포 준비 점검 중 `0052` migration과 API readiness 상수 `0047`의 불일치를 추가 발견. readiness 회귀 테스트를 `0052`로 변경해 예상 RED 1건 확인 후 제품 상수를 `0052`로 수정, 집중 테스트 7 passed/9 skipped 및 API 전체 재검증 906 passed/48 skipped/207 subtests passed. 이 재검증에서 생긴 pytest 임시 폴더 1개도 정확한 경로 확인 후 제거함. WSL 현재 앱 DB revision은 읽기 전용으로 `0051` 확인. WSL DB migration은 아직 실행하지 않음.
- License 보정 커밋 `bcc28c2f`를 `origin/codex/next-user-development`에 push함. readiness 수정은 `0fa6cf3a`로 커밋함. WSL의 `/home/daon/deploy/daon-user`는 다수의 기존 변경과 `secrets/`가 있으며, 현재 실행 컨테이너는 별도의 clean checkout `/home/daon/deploy/daon-user-health-7d168c50`에서 구성됨. 둘 다 변경하지 않음. 프로젝트별 `docs/DEVELOPMENT_ENVIRONMENT.md` 및 정식 QA checkout·DB 명세는 현재 로컬 저장소에서 찾지 못함.
- 추가 보안 gate: `npm audit --omit=dev --json` 현재 결과 critical 1건(`next` 16.3.3, `next/og ImageResponse` 원격 코드 실행 보고), high 22건. [공식 보안 권고](https://github.com/vercel/next.js/security/advisories/GHSA-vcvr-r3jv-pc5j)의 패치 버전은 16.3.6이다. 앱 소스 검색에서 `next/og`/`ImageResponse` 사용은 발견되지 않아 해당 취약 경로의 실제 노출은 확인되지 않았으나, 패키지 finding은 미해결이다. 이번 License/Auto 수정이 도입했다는 증거는 없고 의존성 수정은 승인 범위 밖이다. 운영 병합 gate는 NON-GREEN.
- 오류 횟수: 본 작업 구현 중 Auto 관련 지속 오류 0; License 계약 불일치 1회 및 readiness revision 불일치 1회 발견·보정 완료. 로컬 단위·빌드 검증은 GREEN이나 보안 gate, 실제 PostgreSQL migration, WSL/Oracle 검증은 NON-GREEN/UNVERIFIED.
- 미검증: 실제 PostgreSQL migration 및 기존 연결·Key·허용·기본값 보존, 실제 Media Bridge Server Auto 생성 응답, WSL/Oracle 브라우저·배포, 사용자 인수. 현재 브랜치를 `main`에 병합하거나 배포하지 않음.
- Rollback: 작업 브랜치의 체크포인트 `c17c7e77`이 제품 코드 변경 전 기준. 실제 DB migration 미적용 상태이므로 DB rollback 수행 없음. 기존 연결/Key/허용/Workspace 기본값 변경 없음.
- 다음 조치: 지정된 WSL 정식 QA checkout/DB·Secret 주입 경로를 확정하고 미해결 Critical 보안 finding의 처리 범위를 결정한다. 그 뒤 격리 PostgreSQL에 `0052`를 먼저 적용하여 연결·Key·허용 목록·Workspace 기본값 보존을 검증한다. 이후 WSL 정식 QA, PR, Oracle staging 사용자 인수, 승인 후 병합·재배포 순으로 진행한다. 기존 dirty checkout이나 실행 중 checkout을 덮어쓰지 않으며, 실행하지 못한 항목은 PASS로 승격하지 않는다.

## 보안 업데이트·WSL 격리 QA 후속 (2026-10-04)

- 담당: main agent 어울. 신산님이 Next.js 보안 업데이트와 새 WSL 격리 QA 경로의 권고안을 추가 승인함. 시작 HEAD `c3f0f706`, 작업 브랜치 `codex/next-user-development`, 로컬 worktree clean 확인. 기존 작업 브랜치·worktree를 재사용한다.
- RED 확인: `npm audit --omit=dev --audit-level=critical --json`에서 critical 1, high 22, 종료 코드 1. 대상은 고정 의존성 `next` 16.3.3; 공식 패치 버전 16.3.6. `next/og` 사용은 소스 검색상 없음. 의존성 업데이트 후 audit와 Web/Node/API 회귀를 다시 판정한다.
- 계획된 로컬 임시 자원: 이 worktree 내부 `.npm-cache-security-20261004`. 소유자 어울/본 후속 작업, 목적 npm 레지스트리 패키지 조회·잠금 파일 재생성, 수명 이번 로컬 검증까지, 정리 방법 정확한 절대경로 검증 후 이 폴더만 제거·잔류 0 확인. 기존 `node_modules`는 이 worktree의 일반 디렉터리이며 사용자 자료를 삭제하지 않는다.
- WSL 개발 자원은 새 exact-SHA가 확정되면 기존 `/home/daon/deploy/daon-user-*` 격리 checkout 관례 안에서 정확한 이름·정리 조건을 먼저 기록한다. 기존 dirty 정식 checkout과 실행 중 checkout·DB·Secret은 사전 읽기/백업 없이 변경하지 않는다.
- 로컬 수정: `apps/web/package.json`의 `next` 16.3.3→16.3.6 및 npm이 생성한 `package-lock.json`의 해당 Next/SWC/환경 패키지 항목만 갱신. 설치된 버전 `npm ls next`로 16.3.6 확인.
- GREEN 확인: `npm audit --workspace @daon-user/web --omit=dev`의 critical/high 0, 관리자 화면·BFF Node 테스트 78 passed, Web production build/TypeScript/UI 경계 475파일 위반 0, API 전체 906 passed/48 skipped/207 subtests passed. 저장소 전체 `npm audit --omit=dev`는 critical 0/high 21이며, high는 React Native/Metro/CLI 계열 모바일 의존성으로 웹 대상 audit와 구분한다. WSL·실 PostgreSQL·실 Provider 검증으로 승격하지 않는다.
- WSL 읽기 전용 재점검: 호스트 `SINSAN`, 현재 Daon_User API/Web healthy 및 두 worker running. 기존 `/home/daon/deploy/daon-user` 변경 139개, 실행 중 checkout `/home/daon/deploy/daon-user-health-7d168c50` clean. 기존 경로·서비스는 변경하지 않음.
- 임시 자원 정리: 이번 npm 캐시 `.npm-cache-security-20261004`와 API 전체 테스트의 `.pytest-tmp-security-full-20261004`를 worktree 내부 절대경로로 확인했다. pytest 폴더 내부 36개 링크가 모두 같은 폴더 내부를 가리킴을 검증하고 링크부터 제거한 뒤 두 폴더를 삭제, 잔류 0 확인. 복구 대상 데이터는 없음.

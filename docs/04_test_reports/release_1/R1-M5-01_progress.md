# R1-M5-01 진행 복구 기록

## 현재 상태

| 항목 | 값 |
|---|---|
| 작업지시 | R1-M5-01 Cloud 데이터 저장소·Migration·Transaction·RLS 기반선 |
| 담당 | 어울2 (단일 Writer) |
| 상태 | COMPLETED |
| 현재 단계 | 로컬·exact SHA ysna-server 검증 및 결과 문서화 완료 |
| 작업 위치 | `C:\tmp\Daon_User-r1-m4-06` |
| 브랜치 | `codex/r1-m5-01` |
| 인계 기준 HEAD | `1e737bd817268e7952d7ad2c8a97a68d545ac049` |
| 다음 작업 | 어울1의 독립 검토와 다음 Work Order 판단 |

## 진행 이력

| 시각(KST) | 단계 | 상태 | 변경 파일 | 명령·테스트 결과 | 오류·원인·복구 | 다음 작업 |
|---|---|---|---|---|---|---|
| 2026-07-29 | 착수 | 완료 | 이 파일 | 작업 위치·브랜치·HEAD 및 clean worktree 확인 | 작업지시서 머리말의 기준 Commit `ee71d83...`은 현재 인계 HEAD보다 이전이다. 어울1이 명시한 브랜치와 실제 HEAD `1e737bd8...`을 기준으로 보존하며 되돌리지 않는다. | 승인 정본을 EOF까지 읽는다. |
| 2026-07-29 | 운영 규칙·Skill 확인 | 완료 | 없음 | `AGENTS.md`, `.agents/skills/daon-subagent-delivery/SKILL.md` EOF 확인 | 없음 | 작업 프롬프트와 작업지시서를 읽는다. |
| 2026-07-29 | 작업 프롬프트·작업지시서 확인 | 완료 | 없음 | `R1-M5-01_prompt.md`, `R1-M5-01_work_order.md` EOF 확인 | 없음 | 상세 설계서·구현 계획·테스트 계획을 읽는다. |
| 2026-07-29 | 승인 정본 확인 | 완료 | 없음 | 상세 설계서 v0.7, Release 1 구현계획 v0.9, 테스트계획 v0.7, M4 Exit 검증서 EOF 확인 | 승인 상태와 M5 진입 조건 확인 완료 | 기존 구현과 회귀 영향 범위를 조사한다. |
| 2026-07-29 | 기존 구현·환경 조사 | 완료 | 없음 | M4 API가 SQLite Identity/Authorization와 process-local Notification/Audit를 사용하는 경계를 확인. Python `uv 0.11.2` 확인 | 로컬에는 Docker CLI가 없고 기본 uv cache ACL이 차단됨. 제품 실패가 아니며 로컬은 격리 `UV_CACHE_DIR=C:\tmp\...`, 실제 PostgreSQL은 승인된 ysna-server 격리 Compose에서 검증한다. | PostgreSQL 계약 부재를 재현하는 RED 테스트를 작성한다. |
| 2026-07-29 | TDD RED | 완료 | `services/api/tests/test_cloud_storage.py`, `services/api/tests/test_runtime_http.py` | Cloud Adapter 부재 `ModuleNotFoundError`; Cloud readiness 미연결 `AttributeError`; Production DSN fail-close 부재 1 FAIL을 각각 재현 | 최초 Test 명령은 API source path가 없어 패키지 자체를 찾지 못했다. `PYTHONPATH=services/api/src`로 교정해 대상 모듈 부재 RED를 분리했다. | Migration·Adapter·Readiness 최소 구현 후 GREEN 확인 |
| 2026-07-29 | 의존성 결정 | 완료 | `services/api/pyproject.toml`, `uv.lock` | 공식 자료 기준 Psycopg `3.3.4`는 Python 3.10~3.14/PostgreSQL 10~18 지원, pool extra 제공. Alembic `1.18.5`는 2026-06-25 공개·Python 3.10+ 지원. pgvector `0.8.2`의 PG18 ARM64 가능 공식 Image 계약 확인. `uv lock` 72 packages resolve | 로컬 첫 GREEN은 root workspace 실행이 member dependency를 설치하지 않아 `psycopg` import 실패. `uv run --package daon-user-api`로 정확한 workspace member를 선택해 복구. Secret·DSN 원문 기록 없음. | Runtime GREEN과 정적 품질을 확인하고 배포 자동화 계약을 추가 |
| 2026-07-29 | Cloud 저장소 기반 구현 | 완료 | Migration 4개, `cloud_storage.py`, Runtime, Test | 정적 Cloud 계약 4 PASS·실DB 6 SKIP(로컬 DB 없음). Runtime RED 후 Cloud readiness/production DSN 경계를 구현 | 실제 PostgreSQL 검증은 ysna-server 전용 단계에서 수행 | Runtime GREEN → 운영 자동화 → 전체 로컬 회귀 |
| 2026-07-29 | 로컬 GREEN·회귀 | 완료 | Cloud 구현·Test·배포 계약·의존성 근거 | Cloud 정적 4 PASS/실DB 6 SKIP, API 전체 90 중 80 PASS·환경 SKIP 10, Runtime 신규 2 PASS, 기존 전용 검증 Audit 13·Identity 18·Authorization 22·Runtime Python 11/Node 10·Notification Python 10/Node 21 PASS, Web Build PASS, Ruff·신규 strict mypy PASS, pip-audit 취약점 0, 독립성 위반 0, Toolchain PASS, Secret literal hit 0 | 신규 strict mypy 최초 실행은 기존 모듈 선행 오류와 새 Row generic 오류가 혼합됨. 기존 모듈은 범위 밖으로 유지하고 새 두 모듈을 `--follow-imports=skip`으로 독립 판정, 새 Adapter는 tuple Row로 교정해 0 issue. | 단일 구현 Commit·Push 후 exact SHA ysna-server 격리 PostgreSQL 검증 |
| 2026-07-29 | Commit·Push | 완료 | 구현 17개 파일 | 단일 구현 Commit `6d8d079e3b7c23c54f653a986f9d3dd03fa04607` Push, 구현 SHA Local/Origin/Server 일치 확인 | 첫 Git 쓰기는 공용 Worktree index ACL, 첫 Push는 Sandbox SSH alias/DNS 경계로 차단되어 승인된 Git 환경에서 동일 범위로 복구. 서버 검증에서 발견한 DSN dialect·동시성·PG18 Volume·버전 접미사·Fixture 격리를 같은 단일 Commit에 amend하고 own branch만 force-with-lease했다. 이전 후보 `2b83743`·`a3978c8`·`3d8b28e`는 정본이 아니다. | exact SHA 서버 전체 재검증 |
| 2026-07-29 | ysna-server 환경 복구 | 복구 완료 | 제품 변경 외 검증 Runner·전용 Compose | ARM64/Docker 확인, PostgreSQL 18.4 Healthy, preflight 대상 DB 일치, Migration upgrade/reapply, app role least privilege까지 PASS. 실제 DB Test 10개 중 8 PASS/2 FAIL 후 원인 확정 | (1) PG18 공식 Volume root 변경, (2) 부동 Runner가 uv/Python Pin 불일치, (3) 고정 uv Image가 distroless, (4) source-only PYTHONPATH 누락을 각각 교정. 모두 제품 테스트 전 실행환경 중단. 실제 DB 2 FAIL은 동일 tenant Fixture 간 Audit 누적으로 동시성 결함이 아니며 테스트별 Scope 고유화. 전용 Container·Network·Volume은 매 시도 Trap 정리, 정식 FAILURE_REPORT 0 | `6d8d079`에서 Migration·실DB 10/10·downgrade/restore·자원 불변을 처음부터 재실행 |
| 2026-07-29 | ysna-server 최종 검증 | 완료 | Server Manifest·Summary, Architecture, 완료보고 | exact SHA `6d8d079e...` ARM64: PostgreSQL 18.4/pgvector 0.8.2, Migration upgrade/reapply, 실DB 10/10, actual API cloud ready 200, downgrade/restore/re-upgrade 후 10/10, Audit 불변·Role 최소권한, 공용 자원 Snapshot 불변, 전용 잔여 0 | 앞선 실행환경·Fixture 문제를 모두 분리 복구. 실제 제품 결함 잔여 0, 정식 FAILURE_REPORT 0 | Evidence-only Commit·Push 후 Local/Origin Clean 확인 |
| 2026-07-29 | 최종 HEAD 결속 | 완료 | 본 Progress 포함 Evidence 문서만 | 구현 Commit 이후 Evidence-only HEAD도 동일 ARM64 서버에서 전체 Migration·실DB 10/10·actual API·복구·정리 검증을 재통과했다. 본 행 추가는 제품·Migration·Test 파일 Diff 0이며, 최종 Push 후 서버 exact clean checkout과 구현 Tree 동일성을 다시 확인한다. | 자기 참조 SHA를 문서에 넣어 Commit을 계속 바꾸지 않고 Local/Origin/Server 최종 SHA는 종료 명령 결과와 표준 결과보고로 결속한다. | 최종 Evidence Commit amend·Push → 서버 exact clean binding → Local/Origin Clean |

## 변경 파일

- Cloud 구현·Migration·Runtime: `services/api/**`, `deploy/r1-m5-01/**`
- 자동 검증·의존성: `scripts/verify-api-cloud.mjs`, `package.json`, `uv.lock`
- Architecture·Evidence·보고: `docs/03_architecture/cloud-data-storage-foundation.md`, `docs/03_evidence/release_1/R1-M5-01/**`, 본 Progress와 완료보고

## 검증 요약

- 구현·로컬·서버 검증 완료.
- 승인 경계 변경 없음. 공개 API·데이터 계약 의미 변경 없음.
- 형식적 `FAILURE_REPORT` 누적: 0회.

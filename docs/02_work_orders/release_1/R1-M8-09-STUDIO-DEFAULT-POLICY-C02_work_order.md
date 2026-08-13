# R1-M8-09-STUDIO-DEFAULT-POLICY-C02 수정 작업지시서

## 판정

`STUDIO_DATABASE_UNAVAILABLE` 운영 조사에서 Studio SQL·RLS·Egress Binding은 정상이지만 기존 Workspace의 필수 Studio Canon 3종과 RuleSet 계보가 누락됐음을 확인했다. 신산님은 2026-08-13 Migration `0013` backfill·신규 Workspace Trigger 방식을 승인했다.

본 수정 작업지시서는 기존 `R1-M8-09_work_order.md`의 “신규 Migration 금지”를 이 issue에 한해 명시적으로 대체한다. 그 밖의 Canon·RLS·same-origin·Step-up·보호 변경 계약은 유지한다.

## 작업 계약

| 항목 | 내용 |
| --- | --- |
| issue_id | `R1-M8-09-STUDIO-DEFAULT-POLICY-C02-I001` |
| 승인 설계 | `docs/superpowers/specs/2026-08-13-studio-workspace-default-policy-design.md` |
| 구현계획 | `docs/superpowers/plans/2026-08-13-studio-workspace-default-policy.md` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02_progress.md` |
| 완료 보고 | `docs/04_test_reports/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02_completion_report.md` |
| 개발자 | 어울2 단일 Writer |

## 필수 수행

1. AGENTS.md, 승인 설계, 구현계획, 본 지시서, 실행 프롬프트를 EOF까지 읽고 Hash를 Progress에 기록한다.
2. 구현계획 Task 1→2→3 순서를 TDD RED→GREEN으로 수행한다.
3. Migration `0013`은 기존 Workspace backfill, 신규 Workspace transaction Trigger, 소유 행 rollback을 모두 구현한다. 단, `0013` 비소유 계보가 소유 행을 참조하면 downgrade 전체를 fail-close한다.
4. 운영 Gate에서 확인된 Question 생성형 legacy KnowledgeScope만 승인 설계 §4.1의 exact 조건으로 동일 aggregate v2 append-only 승격한다. 기존 v1 수정, broad key 보완, 다른 불완전 Canon 자동 교정은 금지한다.
5. 정책 누락과 실제 DB 장애의 공개 Safe Error를 구분한다.
6. 실제 PostgreSQL 15 또는 18에서 upgrade·downgrade·reapply·RLS·digest·FK·Trigger를 검증한다.
7. 보호 dirty를 보존하고 허용 파일만 변경한다.

## 허용 파일

- `services/api/migrations/versions/0013_studio_workspace_default_policy.py`
- `services/api/src/daon_user_api/runtime.py`
- `services/api/tests/test_studio_workspace_default_policy_migration.py`
- `services/api/tests/test_studio_workspace_postgres.py`
- `services/api/tests/test_studio_workspace_runtime_http.py`
- `docs/03_evidence/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02/**`
- 본 Work Order·Prompt·Progress·Completion

허용 파일 밖 변경이 필요하면 계획 예외로 분류하고 수정 전에 어울1에게 보고한다.

## 금지

- Egress deny 완화, 자동 승인·자동 전달·자동 지식 등록
- 기존 Canon UPDATE, 기존 Run·Source·Output 소급 변경
- Studio GET lazy write
- SECURITY DEFINER로 RLS 우회
- 브라우저 내부 API 주소·localhost 직접 호출
- 자격정보·DSN·내부 URL·stack을 Evidence나 오류 응답에 기록
- 관련 없는 리팩터링, 보호 dirty stage/restore/delete
- 어울2 commit·push·배포

## 결과 계약

`status | issue_id | 수행 작업 | 변경 결과 | RED/GREEN 및 전체 검증 | 실제 PostgreSQL 증거 | 미해결 | 다음 판단`

승인된 계획 안의 일반 오류는 원인을 해결하며 계속 진행한다. 계획 밖 파일·공개 API·정책값·보안 경계·의존성·파괴적 조치가 필요할 때만 `BLOCKED`로 보고한다.

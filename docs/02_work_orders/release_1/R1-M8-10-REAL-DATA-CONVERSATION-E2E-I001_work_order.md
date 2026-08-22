# R1-M8-10-REAL-DATA-CONVERSATION-E2E-I001 작업지시서

## 정본

- 설계: `docs/superpowers/specs/2026-08-20-real-data-conversation-e2e-design.md`
- 계획: `docs/superpowers/plans/2026-08-20-real-data-conversation-e2e.md`
- 진행: `docs/04_test_reports/release_1/R1-M8-10-REAL-DATA-CONVERSATION-E2E-I001_progress.md`
- 완료: `docs/04_test_reports/release_1/R1-M8-10-REAL-DATA-CONVERSATION-E2E-I001_completion_report.md`

## 수행 지시

1. 위 정본과 `AGENTS.md`를 EOF까지 읽고 SHA, Git root/branch/origin/HEAD/staged 상태를 Progress에 기록한다.
2. 단일 Writer로 TDD를 수행한다. RED·원인·GREEN·변경 파일·명령 결과를 단계마다 Progress에 남긴다.
3. runtime Fixture 삭제 전 exact inventory를 고정한다. 현재 제품 DB Source/fixture가 0이면 삭제하지 말고 `already absent`로 기록한다. 테스트 코드와 Evidence는 삭제하지 않는다.
4. Production Fixture 유입 차단→영역별 오류 분리→일반 대화 Provider 호출→근거 질문 Citation→Studio 저장 순으로 수직 구현한다.
5. 대표 actual Provider는 `UPSTAGE | GROQ | MISTRAL` 중 가용한 1개만 사용한다. 선택과 실제 Run lineage가 다르거나 자동 fallback이 발생하면 실패다.
6. 브라우저는 same-origin, Desktop은 승인 Native command만 사용한다. 내부 주소·Credential·정책 원문을 노출하지 않는다.
7. actual DB/Browser/Provider Gate와 전체 회귀를 구분하고, 미실행 항목을 PASS로 선언하지 않는다.
8. 보호 dirty를 restore/delete/stage하지 않고 commit/push/merge/deploy하지 않는다.
9. 실제 Provider 호출 전 organization·workspace Egress Policy를 기존 API와 Step-up으로 각각 명시 저장한다. Web은 same-origin BFF만 사용하고 현재 비밀번호를 저장·로그·두 단계 사이에 보존하지 않으며, effective projection이 승인 범위와 exact 일치해야 다음 Gate로 진행한다.
10. Step-up은 위 관리자 정책 저장과 License 등 관리자 설정에만 유지한다. 로그인 Session이 유효하고 `EXTERNAL_LLM` 권한과 effective Egress Policy가 허용된 일반·근거 질문 및 Studio 사용에는 비밀번호 재입력·Question authorization 호출·Step-up consume을 사용하지 않는다.
11. 동일 질문 replay는 현재 Notebook Binding을 다시 확인하고 저장 Run의 Provider 종류·외부전송 범위에 따라 현재 `EXTERNAL_LLM` 권한과 exact effective Policy를 재검증한다. 외부 신규 질문은 Provider·목적지·payload 크기·분류·마스킹·redaction이 모두 맞지 않으면 Provider/Run/Egress/Audit 도메인 write0으로 차단한다.
12. 외부전송 authorizer가 Run을 선생성할 때는 완료 저장과 동일한 canonical helper로 Conversation FK와 request fingerprint·Provider kind·egress scope를 완전하게 기록한다. actual PostgreSQL full service 경로에서 최초 호출 후 HTTP replay 200과 Provider 재호출0을 검증한다.
13. 동일 idempotency key 동시 최초 요청은 durable egress decision creator 하나만 Provider owner가 된다. follower는 bounded completed replay만 수행하며 timeout 시 same-key Provider를 탈취하지 않고 retryable fail-close한다. actual PostgreSQL 2-connection에서 Provider1, Run1, Result1, Egress1과 exact Audit 수를 검증한다.
14. in-flight follower도 저장된 complete Run canonical과 현재 logical request fingerprint가 exact 일치해야 한다. mismatch는 즉시 `IDEMPOTENCY_KEY_REUSED`/Provider0/new write0이고, 완료 결과는 fingerprint-aware authoritative replay를 통과한 뒤에만 반환한다.

## 결과 계약

`status | issue_id | fixture inventory/cleanup | 구현 결과 | RED/GREEN | actual Provider/Source/Citation/Studio | 회귀 | 보호 상태 | 미해결 | 다음 판단`

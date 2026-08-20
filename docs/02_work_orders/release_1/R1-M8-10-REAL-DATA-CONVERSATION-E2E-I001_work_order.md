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

## 결과 계약

`status | issue_id | fixture inventory/cleanup | 구현 결과 | RED/GREEN | actual Provider/Source/Citation/Studio | 회귀 | 보호 상태 | 미해결 | 다음 판단`

# R1-M8-09-STUDIO-DEFAULT-POLICY-C02 실행 프롬프트

`AGENTS.md`, 승인 설계 `docs/superpowers/specs/2026-08-13-studio-workspace-default-policy-design.md`, 구현계획 `docs/superpowers/plans/2026-08-13-studio-workspace-default-policy.md`, 수정 작업지시서와 본 프롬프트를 EOF까지 읽는다. 단일 Writer로 구현계획 Task 순서를 TDD RED→GREEN으로 수행하고 단계마다 지정 Progress를 갱신한다. 승인 계획 안의 일반 오류는 중단하지 말고 원인을 해결해 계속한다. 계획 밖 범위·정책·API·데이터·보안·의존성·파괴적 조치가 필요할 때만 수정 전에 `BLOCKED`로 보고한다. commit·push·배포는 수행하지 않는다.

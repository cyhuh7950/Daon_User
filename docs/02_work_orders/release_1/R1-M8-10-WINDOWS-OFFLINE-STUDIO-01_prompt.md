# R1-M8-10-WINDOWS-OFFLINE-STUDIO-01 실행 프롬프트

`AGENTS.md`, 승인 설계 `docs/superpowers/specs/2026-08-14-windows-offline-studio-draft-design.md`, 구현계획 `docs/superpowers/plans/2026-08-14-windows-offline-studio-draft.md`, `R1-M8-10-WINDOWS-OFFLINE-STUDIO-01_work_order.md`와 본 프롬프트를 EOF까지 읽는다. 프로젝트 Custom Agent 어울2 단일 Writer로 구현계획 Task 1→8을 TDD RED→GREEN 순서로 수행하고 각 단계·오류·원인·복구·변경 파일·테스트·다음 작업을 지정 Progress에 기록한다. 승인 계획 안의 일반 오류는 중단하지 말고 원인을 해결해 계속한다. 계획 밖 범위·공개 API·데이터 계약·보안 경계·의존성·파괴적 조치·외부 배포가 필요할 때만 수정 전에 `BLOCKED`로 보고한다. 보호 dirty를 건드리지 않고 commit·push·merge·배포는 수행하지 않는다.

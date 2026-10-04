# 라우터 Auto 모델 작업현황

- 단계: 설계서 승인·구현 계획 작성, 계획 검토 대기 (2026-10-04)
- 담당: main agent 어울
- 기준: `codex/next-user-development`, 시작 HEAD `980850b972b56e84400226ccfed88ddcee142e1b`
- 신산님 결정: OmniRoute, OpenRouter, Media Bridge Server 등 라우터의 Auto 개념만 다룬다. `combo`는 제외한다. 모델 미지정의 기준 모델 동작은 Auto 명시 선택과 구분한다.
- 변경 파일: `docs/superpowers/specs/2026-10-04-router-auto-model-design.md`, `docs/superpowers/plans/2026-10-04-router-auto-model.md`, 이 현황 파일. 제품 코드·DB·설정 변경 없음.
- 확인: OmniRoute QA `GET /v1/models` HTTP 200, 867개 ID 중 `auto`와 `combo` 없음. QA 카탈로그/허용 목록에는 기존 `auto`가 각각 있음. OpenRouter 공식 문서와 공개 `/api/v1/models`에서 실제 ID `openrouter/auto` 확인. 기존 UI·API는 OmniRoute만 Auto를 특수 취급하고 `CUSTOM`/OpenRouter는 조회 모델 중심임을 코드에서 확인.
- 테스트: 설계 단계이므로 코드 테스트 미실행. 오류 횟수 0.
- 미검증: Media Bridge Server의 실제 Auto 생성 응답, 새 migration/API/UI, WSL 브라우저 동작, 사용자 인수.
- 다음 조치: 신산님의 구현 계획 검토 및 실행 방식 선택. 이후 승인된 범위에서 TDD 구현·로컬 검증.

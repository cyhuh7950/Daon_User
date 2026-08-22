# R1-USER-AUTH-UI-01 작업지시서 — 인증 3화면 재작업

## 목표

승인 설계 `docs/superpowers/specs/2026-08-12-user-auth-screen-separation-design.md`와 계획 `docs/superpowers/plans/2026-08-12-user-auth-screen-separation.md`를 구현한다.

## 필수 구현

- 로그인 화면은 사용자 ID·비밀번호·로그인과 `가입하기`, `비밀번호를 잊으셨나요?` 전환만 제공한다.
- 가입 화면은 가입 단계와 이메일 인증 단계를 순차 표시하며 이메일 인증·인증 재전송은 인증 단계에만 둔다.
- 비밀번호 재설정 화면은 재설정 메일 요청 단계와 토큰·새 비밀번호 설정 단계를 순차 표시한다.
- 로그인 전 명칭은 `비밀번호 재설정`으로 고정하며 로그인 후 실제 비밀번호 변경은 계정 설정의 별도 범위다.
- Password/Token 즉시 정리, 중복 제출 차단, Safe Error 비반사, same-origin BFF를 유지한다.

## 허용 파일

- `apps/web/lib/auth-pane.jsx`
- `apps/web/app/globals.css`
- `scripts/tests/auth-pane.test.mjs`
- `scripts/tests/desktop-tauri-shell.test.mjs`
- 본 작업의 설계·계획·작업지시·프롬프트·Progress·Completion

## 금지

- API·DB·OpenAPI·Native·Compose 변경
- Credential 조회·기록, 신규 인증 API·메일 링크 Route
- 관련 없는 리팩터링, 기존 사용자 dirty 복원·삭제·stage
- RED 확인 전 제품 코드 수정

## 필수 검증

- focused AuthPane unit/source + 실제 React 행동 RED→GREEN
- 관련 Workspace/Desktop 회귀
- Web lint/build, Product boundary
- Password/Token/내부주소 정적 scan, `git diff --check`

## 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

Progress: `docs/04_test_reports/release_1/R1-USER-AUTH-UI-01_progress.md`

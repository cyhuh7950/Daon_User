# R1-USER-AUTH-UI-01 완료보고

## 판정

`COMPLETED`

## 판단 이유

- 기본 로그인 화면은 사용자 ID·비밀번호 입력 2개, 로그인 버튼, 가입하기·비밀번호 재설정 전환 링크만 렌더링한다.
- 가입 화면은 사용자 ID·메일 주소·비밀번호 3개와 가입만 표시하고, 성공 뒤 인증 토큰·이메일 인증·인증 재전송 단계만 표시한다.
- 비밀번호 재설정 화면은 identifier·재설정 메일 요청만 표시하고, 성공 뒤 재설정 토큰·새 비밀번호·비밀번호 재설정 단계만 표시한다.
- 화면 전환과 요청 종료 시 Password·Token을 지우며 로그인 실패 뒤 Password가 비워짐을 실제 React 테스트로 확인했다.
- 요청 중 모든 현재 화면 버튼을 비활성화하고 중복 로그인 요청이 1회만 전송됨을 확인했다.
- 오류 원문·내부 URL·Stack을 반사하지 않고 기존 Safe Error 문구만 표시한다.
- `auth-api.js`를 변경하지 않아 기존 DTO, Cookie/CSRF 흐름, same-origin `/bff/api/auth/*`, Workspace redirect 의미를 보존했다.
- 첫 입력 focus, form Enter submit, `aria-labelledby`, `role=status`, 기존 도움말 tooltip과 화면·폰트 CSS를 유지했다.

## 변경 파일

- `apps/web/lib/auth-pane.jsx`
- `apps/web/app/globals.css`
- `scripts/tests/desktop-tauri-shell.test.mjs`
- 승인 설계·계획·작업지시·Progress·WSL 인계·본 Completion 문서

`scripts/tests/auth-pane.test.mjs`, API·DB·OpenAPI·Native·Compose는 변경하지 않았다.

## TDD 및 검증 결과

- RED 1: 기존 로그인 DOM에 `비밀번호를 잊으셨나요?` 전환이 없어 focused exit 1.
- RED 2: 전환 동작의 link 표현 class가 없어 focused exit 1.
- GREEN actual React: 1/1 PASS. 로그인 input 2개, 가입 input 3개, 가입 성공 뒤 인증 input 1개, 재설정 요청 input 1개, 요청 성공 뒤 설정 input 2개를 확인했다.
- AuthPane source 계약: 3/3 PASS.
- 기존 Web Login actual React: 1/1 PASS.
- Desktop 전체 회귀: 26/26 PASS.
- Product Workspace·Workspace 회귀: 20/21. 실패 1건은 auth 무관 기존 expected-state 누락으로 분리했다.
- Workspace lint: 16파일 PASS.
- Product UI boundary: 281파일, violation 0, boundary error 0.
- Web production build·TypeScript: exit 0; Web boundary 269파일, violation 0, boundary error 0.
- 민감정보 저장·로그·URL 및 내부주소 정적 scan: 0건.
- `git diff --check`: PASS.
- Staged: 0개.

Cargo 후속 검증 과정에서 기존 Node `DEP0190` deprecation warning 1건이 출력됐으나 테스트 실패는 0건이다.

## 제외·미해결

- `scripts/tests/workspace.test.mjs:206`은 현재 정본 상태의 `studioLocks`·`studioStatus`·`studioSafeError`를 expected object에 포함하지 않아 1건 실패한다. auth 범위 밖이므로 수정하지 않았다.
- 실제 Browser, 운영 유사 Docker, 운영 배포, 실제 가입·로그인, Credential 입력은 이번 구현 단계에서 수행하지 않았다.
- Commit, Push, PR, Deploy는 수행하지 않았다.
- 저장소의 기존 Desktop·Mobile·Model Connection·다른 작업 문서 dirty 변경은 복구·삭제·stage하지 않고 보존했다.
- 최신 설계와 최종 diff의 외부 독립 검증 및 신산님의 최종 완료 판단이 남아 있다.

## 2026-08-13 ysna Web 재배포

- exact commit `bc3ecf0abef75b32d9db84b762fcdd62f94502a0`을 ysna-server에 배포했다.
- 이전 Web image rollback tag를 확보한 뒤 Web만 build/recreate했다. DB·API·worker·공용 자원은 변경하지 않았고 migration `0012`를 유지했다.
- server Web build·TypeScript·9 pages와 Product boundary 291/0, Web health, public root/BFF 200을 확인했다.
- 실제 Chrome에서 로그인 input 2개와 로그인·가입·비밀번호 재설정 전환, 가입 input 3개, 재설정 요청 input 1개를 actual click으로 확인했다. 내부주소 0, Credential 입력·submit 0이다.
- 상세 증거: `docs/03_evidence/release_1/R1-USER-AUTH-UI-01/ysna-auth-ui-deployment-evidence.md`.

status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단
--- | --- | --- | --- | --- | --- | ---
COMPLETED | R1-USER-AUTH-UI-01-I001 | 인증 3화면·순차 단계 분리와 ysna Web-only 배포 | AuthPane·link 스타일·actual React 테스트·Progress/Completion·배포 Evidence | 로컬 TDD·회귀·build/boundary 및 서버 build/health, production Chrome 공개 DOM PASS; auth 무관 Workspace 1건 분리 | 자격 없는 성공 submit 이후 단계 미검증, Workspace expected-state 1건은 범위 밖 | 어울1 최종 배포 증거 검토

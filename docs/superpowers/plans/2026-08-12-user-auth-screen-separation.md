# 사용자 인증 3화면 분리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` and follow the Daon one-writer delivery contract.

**Goal:** 로그인·가입/이메일 인증·비밀번호 재설정을 목적별 세 화면과 단계별 UI로 분리한다.

**Architecture:** 기존 `AuthPane` local state와 `authApi`를 재사용한다. `login | signup | password-reset` 화면 상태와 가입·재설정 내부 단계 상태를 두고 현재 단계 DOM만 렌더링한다.

**Tech Stack:** React 19, Next.js, Node test runner, Vite/React DOM harness

## Global Constraints

- 로그인 DOM에는 ID·Password·로그인·가입 링크·비밀번호 재설정 링크만 존재한다.
- 가입/인증과 재설정 요청/설정은 각각 단계별로 동시 노출하지 않는다.
- 기존 API·DTO·Cookie/CSRF·same-origin·Workspace redirect 의미를 유지한다.
- Password/Token은 전환·요청 종료 시 지우고 저장·로그·URL에 남기지 않는다.
- API·DB·OpenAPI·Native·Compose를 변경하지 않는다.

---

### Task 1: 세 화면·단계 행동 RED

**Files:** `scripts/tests/desktop-tauri-shell.test.mjs`

- [ ] 로그인 초기 DOM이 input 2개와 로그인·가입하기·비밀번호 재설정 링크만 갖는 테스트를 작성한다.
- [ ] 가입 단계 3필드와 가입 성공 후 인증 단계만 표시되는 테스트를 작성한다.
- [ ] 재설정 요청 단계와 요청 성공 후 새 비밀번호 설정 단계만 표시되는 테스트를 작성한다.
- [ ] 전환·실패 뒤 password/token clear, 중복 요청 1회, Safe Error, exact payload를 검증한다.
- [ ] focused 실행에서 기존 통합 복구 화면 때문에 정확히 RED인지 확인한다.

### Task 2: AuthPane 최소 GREEN

**Files:** `apps/web/lib/auth-pane.jsx`, 필요한 경우 `apps/web/app/globals.css`

- [ ] `login | signup | password-reset` 배타 화면과 가입·재설정 내부 단계를 구현한다.
- [ ] 가입 성공 시 인증 단계, 재설정 메일 성공 시 새 비밀번호 단계로 전환한다.
- [ ] 인증·재전송은 가입 인증 단계에만, confirm reset은 재설정 설정 단계에만 연결한다.
- [ ] 전환·요청 종료의 민감값 정리와 busy/focus/accessibility 계약을 유지한다.
- [ ] focused GREEN을 확인한다.

### Task 3: 회귀·배포

**Files:** Progress, Completion, ysna deployment evidence

- [ ] Auth focused/source/Web login, Desktop·Workspace 회귀를 실행한다.
- [ ] lint, Web build, Product boundary, secret/internal URL scan, `git diff --check`를 실행한다.
- [ ] 단일 목적 Commit·Push 후 ysna-server에서 exact SHA로 Web만 rebuild/recreate한다.
- [ ] 운영 Browser에서 로그인 input2, 전환 링크2, 가입/재설정 단계와 same-origin Network를 확인한다.
- [ ] 기존 API 건강 상태와 보호 `backups/`, `secrets/`, 공용 자원을 보존한다.

## 완료 조건

- 로그인 화면은 ID·Password·로그인·가입하기·비밀번호 재설정 링크만 표시한다.
- 가입 화면은 가입 단계와 이메일 인증 단계를 동시에 펼치지 않는다.
- 비밀번호 재설정 화면은 메일 요청과 새 비밀번호 설정 단계를 동시에 펼치지 않는다.
- 기존 인증 API 6종 payload와 same-origin 의미가 보존된다.
- 자동 테스트·Build·Boundary·운영 화면 검증이 통과한다.

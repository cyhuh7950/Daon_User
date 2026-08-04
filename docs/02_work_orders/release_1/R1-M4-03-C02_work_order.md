# R1-M4-03-C02 작업지시서 — 이메일 기반 초기 가입·인증·비밀번호 재설정

## 목적

기존 OIDC/PKCE 인증을 유지하면서, 최초 접속 사용자가 ID·비밀번호·메일 주소로 가입하고 메일 인증 후 로그인할 수 있도록 한다. 비밀번호 분실 시 인증된 메일 주소로 일회용 재설정 링크를 발송한다.

## 범위

- 가입 요청: 사용자 ID, 이메일, 비밀번호 입력 및 형식·강도 검증
- 비밀번호는 Argon2id 해시만 저장하며 원문·로그 기록 금지
- 이메일 인증 토큰 발급·만료·1회 사용·재발송 제한
- 인증 완료 사용자에 대한 Web 세션 발급(기존 HttpOnly `__Host-daon_session` 경계 유지)
- 비밀번호 재설정 요청·일회용 토큰 검증·새 비밀번호 설정
- 메일 발송은 SMTP 환경변수 기반 서버 어댑터로 격리
- SMTP 미설정 시 가입·재설정은 안전한 `EMAIL_DELIVERY_UNAVAILABLE`로 종료
- 가입·인증·재설정 Audit 기록과 요청 속도 제한

## API 계약

- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/verify-email`
- `POST /api/v1/auth/resend-verification`
- `POST /api/v1/auth/password-reset/request`
- `POST /api/v1/auth/password-reset/confirm`
- 성공 응답에는 비밀번호·토큰 원문을 포함하지 않는다.
- 존재 여부를 노출하지 않는 응답으로 계정 열거를 방지한다.

## 데이터·보안 계약

- 사용자 ID·이메일은 정규화·유일 제약
- 비밀번호 Argon2id 해시, 이메일·인증/재설정 토큰은 digest만 저장
- 토큰 TTL·1회 사용·재사용 거부·최대 시도 및 IP/식별자 rate limit
- 인증 전 계정은 로그인·보호 리소스 접근 불가
- 비밀번호 재설정 성공 시 기존 세션·refresh family를 철회
- SMTP 자격정보는 `.env`/Docker 비밀로만 주입

## 허용 파일

- `services/api/src/daon_user_api/identity.py`
- `services/api/src/daon_user_api/runtime.py`
- `services/api/tests/` 관련 인증 테스트
- `services/api/pyproject.toml`, `uv.lock` (필요한 보안 의존성만)
- `deploy/daon-user/compose.yaml`, `.env.example` (비밀값 없는 SMTP 키 목록)
- 관련 작업 진행·결과 보고 문서

## 제외

- 기존 OIDC Provider 제거·대체
- 비밀번호 원문 또는 SMTP 키 저장
- 운영용 기본 관리자 계정 자동 생성
- 브라우저의 API 절대주소 호출

## 완료 증거

- 단위·API 테스트: 정상/중복/약한 비밀번호/만료·재사용 토큰/계정 열거 방지/세션 철회
- Argon2id 해시와 민감값 로그·응답 비노출 검사
- 기존 OIDC·세션·권한 회귀 테스트 통과
- 진행 기록: `docs/04_test_reports/release_1/R1-M4-03-C02_progress.md`

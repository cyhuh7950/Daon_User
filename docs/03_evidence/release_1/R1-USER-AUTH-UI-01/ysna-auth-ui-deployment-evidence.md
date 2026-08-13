# ysna-server Auth UI Deployment Evidence

- issue: `R1-USER-AUTH-UI-01-I001-YSNA-DEPLOY`
- executed: `2026-08-13T21:40+09:00`
- server root: `/home/ubuntu/deploy/daon-user`
- deployed commit: `bc3ecf0abef75b32d9db84b762fcdd62f94502a0`
- previous server commit: `adafcb363a07d17bd779bf246143c85f11532834`
- database migration: 기존 `0012` 유지, DB 명령·변경 0

## Preflight와 rollback

- 서버는 기존 detached `adafcb3`였으며 dirty는 보호 untracked `backups/`, `secrets/`뿐이었다. 대상 tree와 보호 경로 충돌은 0이었다.
- 배포 전 Web container `44f810ebd03a`, image `sha256:3a84f357...c3685`, healthy였다.
- rollback tag `daon_user-web:rollback-auth-bc3ecf0-20260813`를 같은 image ID로 확보했다.
- exact commit을 fetch·검증한 뒤 detached `bc3ecf0`로 전환했고 보호 경로는 유지됐다.

## Build와 Web-only switch

- tracked compose `deploy/daon-user/compose.yaml`와 서버 `.env`를 이용해 Web만 build했다.
- Next production compile·TypeScript·9 pages PASS, Product UI boundary 291 files, violation 0, boundary error 0.
- new Web image `sha256:24e61210...ad79f`를 만들고 `--no-deps web`만 recreate했다.
- deployed Web container `b8c7c404dd25`, new image, healthy다.
- API `a19a878f3663`/image `6b8754d7...a655f79` healthy, worker `b7902ce2dc1e`/image `55225fa6...60d3c4` running, object storage `e5c606a43bf8` healthy로 ID와 image가 배포 전후 동일하다.
- shared-db `c962c98c7970`, nginx-proxy-manager `337026a012e2`, netdata `8cab33d7f80b`도 실행 상태와 ID가 동일하다.

## Health와 actual Chrome DOM

- public root `https://daon-user.sinsan.kr/` 200, same-origin `/bff/shell/runtime` 200, Web recent traceback/exception/fatal/panic count 0.
- 실제 Chrome 로그인 화면: input 2개(`login-id`, `password`), 로그인 button 1개, `가입하기`와 `비밀번호를 잊으셨나요?` link-style 전환 2개, 내부주소 token 0.
- 로그인 화면 safe screenshot SHA-256: `5c6e5fc79649d162287eda2fea0cb48b6d087990957cddf30ece92105e7a0855`.
- `가입하기` actual click 뒤 가입 화면은 input 3개(`signup-login-id`, `email`, `signup-password`)와 `가입`, `로그인으로 돌아가기`만 표시했다.
- 로그인으로 복귀한 뒤 `비밀번호를 잊으셨나요?` actual click 시 input 1개(`reset-identifier`)와 `재설정 메일 요청`, `로그인으로 돌아가기`만 표시했다.
- 신규 Credential 입력, 가입/재설정 submit, Cookie/Token 조회는 0이다. Browser 탭은 검증 후 정리했다.

## Postflight

- server exact HEAD `bc3ecf0`, Git status는 보호 untracked `backups/`, `secrets/`만 남았다.
- DB/API/worker/object-storage/shared-db/proxy/netdata 변경 명령 0, migration downgrade 0.
- Web rollback image tag는 보존했다. rollback 조건은 발생하지 않았다.

## 판정

`DEPLOYMENT_PASS / PUBLIC_BROWSER_DOM_PASS`. Exact commit Web-only 배포, build/boundary, health, 공개 로그인·가입·재설정 화면 전환을 검증했다. 실제 자격 기반 가입 성공 후 인증 단계와 재설정 메일 성공 후 설정 단계는 승인대로 submit하지 않았다.

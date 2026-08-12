# R1 사용자 제품 분리 Task 8 Native BFF 보정 작업지시서

## 1. 승인·Issue·Writer

- Work Order ID: `R1-USER-PRODUCT-SEPARATION-TASK8-C02`; 기존 Issue ID `R1-USER-PRODUCT-SEPARATION-TASK8-01-I001`을 유지한다.
- 신산님은 2026-08-12 Browser Cookie BFF와 Native Bearer BFF의 분리 보정을 승인했다.
- 공식 작업공간은 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`이며 한 명의 어울2만 Writer다.
- 착수 전 `AGENTS.md`, `docs/superpowers/specs/2026-08-12-native-bff-channel-design.md`, `docs/superpowers/plans/2026-08-12-native-bff-channel.md`, 원 Task 8/D01/C01 Progress·Completion을 EOF까지 읽고 Hash를 Progress에 기록한다.

## 2. 허용 파일

- Modify: `apps/web/lib/bff-api-proxy.js`
- Modify: `apps/web/app/api/v1/[...path]/route.js`
- Modify: `scripts/tests/api-bff-runtime.test.mjs`
- Modify: `services/api/tests/runtime_process_probe.py` — Actual Next Process가 필수 공개 HTTPS Origin을 누락한 검증 정합화만 허용
- Modify: `apps/web/lib/notification-inbox-api.js`
- Modify: `apps/web/lib/recovery-api.js`
- Modify: `scripts/tests/notification-inbox-ui.test.mjs`
- Modify: `scripts/tests/recovery-api.test.mjs`
- Create/append: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-TASK8-C02_progress.md`
- Create: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-TASK8-C02_completion_report.md`
- 본 작업지시·프롬프트·설계·계획 외 API·OpenAPI·Rust·Nginx·Compose·DB·Migration·Lock·의존성 수정과 Stage를 금지한다.

## 3. 구현 계약

- 승인 설계의 Browser/Native 채널, exact Route·Method, Login/Refresh 예외, Bearer, Cookie/Set-Cookie, timeout/body/redirect/Safe Error 경계를 그대로 TDD 구현한다.
- Browser `/bff/api`의 기존 Session Cookie·same-origin CSRF·Recovery·Workspace 동작을 보존한다.
- `/api/v1`만 Native Proxy를 사용하고 다른 public route·Reverse Proxy를 변경하지 않는다.
- Web Notification·Inbox·Recovery Adapter는 DTO·Method·Header 의미를 보존한 채 `/bff/api` Browser 채널로 이관하며 Web 제품의 `/api/v1` 직접 호출을 0으로 만든다.
- Recovery Native 응답은 1MiB 상한과 계산된 Content-Length를 사용하고 공개 Transfer-Encoding은 없어야 한다. JSON/PDF 요청·응답 Content-Type은 parameter 제거 후 exact media type만 허용한다.
- 실제 Password·Credential·운영 요청·배포·Container·DB 변경은 수행하지 않는다.
- 사용자 삭제25·Cargo 동일 Blob·Native Evidence·기존 미추적 문서를 복원·수정·Stage하지 않는다.

## 4. 필수 검증

```powershell
node --test scripts/tests/api-bff-runtime.test.mjs
npm run verify:api-runtime
uv run --isolated --with pytest==9.0.3 pytest services/api/tests -q
node scripts/verify-openapi-contract.mjs
npm run build --workspace @daon-user/web
npm run verify:product-ui-boundary
git diff --check
```

- 실제 Next production process 또는 동등 Route Handler 실행에서 Native login 빈 JSON non-404, Session 무자격 401, Cookie/unknown path upstream0을 확인한다.
- 실제 Next production process에서 Native JSON/PDF framing, Recovery Content-Length 존재·Transfer-Encoding 부재, Web Notification·Inbox Browser 경로를 실행 검증한다.
- Actual Process Probe는 내부 Next HTTP listener와 공개 HTTPS Origin을 분리한다. `DAON_PUBLIC_GATEWAY_URL=https://localhost:<web_port>`를 process env에 주입하고 Browser write Origin도 그 공개 Origin을 사용한다. 제품 BFF의 HTTPS 강제를 완화하거나 localhost 예외를 제품 코드에 추가하지 않는다.
- Browser BFF 회귀, sensitive value 반사0, internal address/source literal 금지, 허용 Diff와 Dirty 보존을 기록한다.
- Sandbox/OneDrive 환경 오류와 제품 실패를 분리하며 같은 기능 오류를 우회하지 않는다.

## 5. 결과 계약

- 형식: `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`.
- 필수 계약·회귀·Build·보안·보존 증거가 모두 있어야 `COMPLETED`다.
- 완료 후 독립 검토와 별도 Commit·Push·PR 승인 전 Git 원격 변경과 Task 8/D01 배포 재개를 금지한다.

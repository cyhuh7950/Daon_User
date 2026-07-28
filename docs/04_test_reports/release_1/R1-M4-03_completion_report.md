# R1-M4-03 개발 완료 보고서

## 판정

`COMPLETED` — 승인된 Identity Core·SQLite 재시작 경계·Audit 연결·OpenAPI 구체 계약과 결정적 Verifier를 구현했다. R1-M4-03 범위의 필수 테스트는 모두 통과했다.

## 구현 결과

- OIDC Authorization Code+PKCE S256 거래, issuer·client·audience·redirect exact allowlist, state·nonce·verifier 원문 비저장
- 검증 완료 Provider Protocol과 Claim issuer·audience·nonce·exp 재검증
- opaque Web Session과 Native access·refresh digest-only 저장, 회전·만료·replay family/session 철회
- User·Tenant·Membership·Session·Refresh family/token·Device·OIDC transaction·Step-up의 SQLite schema v1, FK·WAL·transaction·restart
- Device 등록·신뢰·철회·last_seen 갱신과 후속 Sync-key 폐기 요구 이벤트
- 제거 불가 최소 7개와 조직 add-only Action, actor·session·device·tenant·action·target·policy 결합 1회용 Step-up
- login·refresh·session/device revoke·Step-up 성공/거부 Audit 및 Audit append 실패 DB rollback
- Session/OIDC/Refresh/Device/Step-up OpenAPI concrete schema와 Web same-origin·Native HTTPS 전달 의미

## 검증 근거

| 검증 | 결과 |
| --- | --- |
| `npm run verify:api-identity -- --no-write` | PASS, 11 tests, 7 actions, source `77FE795A...B4C3` |
| `npm run verify:api-audit -- --no-write` | PASS, 13 tests, hash chain valid |
| `npm run verify:openapi-contract -- --no-write` | PASS, 41 paths·64 operations·35 schemas |
| Python compileall·Package export | PASS |
| Workspace | PASS, 34/34 |
| Independence·Toolchain | PASS, violations 0 |
| Diff·Lockfile | `git diff --check` PASS, `package-lock.json`·`uv.lock` 변경 0 |
| Secret 정적 점검 | 변경 파일의 Secret assignment/private-key pattern 0 |

## 기준선 제한

- 변경 Worktree 전체 Quality Gate는 공용 Local Service `uv pytest` 대기에서 1,800.6초 timeout 됐다. 강제 반복하지 않았다.
- exact base `1fba9576df85a6108dcf1c5d9a790afb3775d607`의 동일 Gate도 기존 공용 실패 12건으로 FAIL이었다. Security·Contract·Independence는 PASS였다.
- 격리 `npm ci` 후 전체 Node는 변경 범위 밖 iOS Permission/Search 계약 10건이 실패했다. exact base의 동일 iOS 시험에서도 같은 10건이 재현됐다.
- 위 항목은 R1-M4-03 기능 실패로 귀속하지 않는다. 본 작업은 테스트계획의 TP 의무 보고 시점에 도달하지 않았다.

## 정직성 경계

Fake Provider는 deterministic Protocol 시험일 뿐 외부 IdP 로그인 증거가 아니다. 실제 HTTP Route, Web Cookie 속성·CSRF, PostgreSQL/RLS·durable Audit outbox, Device Sync-key 실제 폐기는 각각 M4-05·M5·M6 후속 범위다.

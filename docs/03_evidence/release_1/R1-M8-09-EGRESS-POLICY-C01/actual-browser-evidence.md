# Actual Browser Policy Gate Evidence

- 기록 시각: `2026-08-13T11:32:59+09:00`
- current source: `apps/web/lib/egress-policy-api.js` SHA-256 `B0DD95091630CFEC8016233EF313501D93D649A264C4E1BA6B02621E4311DA93`
- production build ID file SHA-256: `5DE7211D969DEE7D845BD02B0C632A42CD028FC35222928A0E8C18C7B470F5A5`
- build: Next production compile·TypeScript·8 pages PASS, Product Boundary 269 files/0 violations
- 격리 실행: current-source API `127.0.0.1:18480`, Web standalone `127.0.0.1:14180`; 공용 Web `3330`과 기존 container는 미변경
- 자격 보호: test credential·Cookie·Step-up authorization 원문은 증거·로그·파일에 기록하지 않았다.

## 실제 Browser 여정

| 단계 | same-origin 경로 | 결과 |
| --- | --- | --- |
| 로그인 | `POST /bff/api/auth/login` | 성공, Workspace route 이동·session cookie 의미 성립; cookie 원문 미수집 |
| 세션 context | `GET /bff/api/session` | Organization/Workspace context가 정책 adapter에 전달됨 |
| 정책 조회 | `GET /bff/api/workspaces/{workspace_id}/egress-policy` | 200, UI render 성공 |
| Step-up | `POST /bff/api/session/step-up` | 첫 실행은 필수 Idempotency-Key 누락으로 `INVALID_REQUEST`; RED→최소 교정 후 성공 |
| Organization 저장 | `POST /bff/api/organizations/{organization_id}/egress-policy-versions` | 201 의미의 저장·refresh 성공 |

Browser가 호출한 주소는 `/bff/api/...` 상대 경로뿐이며 내부 API `18480`은 Web server-only environment로만 사용했다.

## 화면에서 검증한 8필드

- mode: `allow_approved_external` 저장 후 effective Workspace deny로 `deny_external` 표시
- max bytes: `4096`
- provider kinds: `external_api`
- destinations: `provider.example`
- classification: `restricted`
- masking required: `true`
- redaction required: `true`
- required approver: `organization_admin`

## DB·정리 대조

- Organization policy version: `2`
- succeeded `egress_policy.activate` Audit: `1`
- effective Workspace policy: `deny_external`
- Runs / RunResults / ModelAttempts: `0 / 0 / 0`
- 격리 API/Web listener 종료, test SQLite/log 삭제, Browser disposable DB 삭제
- `daon_r1_m8_09_*_it_*` remaining=`0`, public `local-postgres` running=`true`

## OPEN

ready Source/provider fixture가 없어 외부 Question 제출 자체의 deny 클릭은 수행하지 않았다. 따라서 실제 Browser policy 관리 Gate는 PASS이고, external Question deny 요청의 provider transport 0 클릭 증거만 OPEN이다.

# R1-M4-05 완료보고

## 판정

**COMPLETED — 승인된 FastAPI Runtime·same-origin BFF 실행 경계를 구현하고 필수 직접 검증을 완료했다.**

최종 전체 Quality Gate에서 기존 Desktop isolated Cargo 단계가 exit 101로 한 차례 실패했으나, R1-M4-05 또는 Desktop 제품 파일을 변경하지 않은 상태에서 즉시 실행한 `npm run verify:desktop-unit`가 JavaScript 25개와 Rust 17개를 모두 통과했다. 어울1 판정에 따라 병렬 임시 Cargo 환경 transient로 분류하며, 최종 clean 전체 Gate는 GitHub CI에서 다시 판정한다.

## 판단 이유

- 실제 M4-03 Session 검증 결과만 M4-04 Authorization에 전달하며 Client tenant·user·role header를 신뢰하지 않는다.
- 실제 M4-02 Audit store와 M4-04 현재 role·ACL·policy를 호출하는 6개 FastAPI route를 제공한다.
- Web은 실제 Next production process의 same-origin `/bff/api/...`만 사용하며 내부 API 설정은 server route에만 존재한다.
- 실제 Python API child가 graceful lifecycle을 완료하고 exit 0으로 종료한다. 같은 port 재기동과 두 번째 exit 0, 최종 owned process·listener 0을 확인했다.
- production Client bundle, BFF 응답, process log에서 내부 API URL·credential·DB path가 검출되지 않았다.
- OpenAPI의 optional tenant-wide Audit 조회는 현재 tenant role을 실제 repository에서 확인하고 허용·거부 Audit를 기록한다.

## 조치

- Branch `codex/r1-m4-05`의 단일 Commit을 Push한다.
- PR·CI·Merge·ysna-server 배포는 어울1이 수행한다.
- GitHub CI에서 전체 Quality Gate의 clean PASS를 최종 확인한다.

## 주요 변경

### API Runtime

- `runtime.py`: App factory, 설정 검증, dependency 주입, Trace, 안전 오류, 요청 경계, live/ready, 4개 업무 route와 shutdown state
- `main.py`·`__main__.py`: 실제 Uvicorn entrypoint, trusted proxy, bounded graceful shutdown, 정상 exit 0
- `identity.py`: 기존 `validate_access`를 그대로 선행하는 안전 Session projection `describe_access` 추가
- `authorization.py`: workspace 또는 tenant role 기반 Audit read authorization 추가
- API dependency를 기존 lock의 exact `fastapi==0.139.2`, `uvicorn==0.51.0`, test `httpx==0.28.1`로 고정

`identity.py` 전후 계약은 다음과 같다.

- 변경 전: 검증된 `IdentityPrincipal`만 반환해 HTTP session schema의 `client_kind`·`expires_at`을 안전하게 구성할 수 없었다.
- 변경 후: 기존 credential 검증·Audit·TTL 판정을 변경하지 않고, 검증 완료 session ID의 `client_kind`·`access_expires_at`만 안전 projection으로 추가 조회한다.
- 회귀: Identity 18개와 Authorization 22개가 모두 통과했다.

### Web BFF

- 고정 route·method·query·header allowlist
- redirect `manual`, hop-by-hop·client claim·Location·Server header 차단
- server-only `DAON_API_INTERNAL_URL`; production HTTPS와 test/development loopback fail-close
- 연결·timeout·redirect·configuration 오류의 안전 envelope

### 계약·운영

- OpenAPI `SafeErrorCode`를 Runtime/BFF가 실제 반환하는 20종으로 정합화
- API Runtime Architecture와 Web/API README 추가
- Quality Gate의 API build capability를 실제 Runtime verifier로 연결

## 실제 HTTP·Process 증거

| 검증 | 요청 경계 | 상태·Trace |
| --- | --- | --- |
| API live | `/health/live` | 200 |
| API ready | `/health/ready` | 200 |
| Web session direct | `/api/v1/session` | 200 · `trace-process-001` |
| Authorization | `/api/v1/workspaces/workspace-001/authorization/evaluations` | 200 · `trace-process-001` |
| Browser 관점 BFF | same-origin `/bff/api/session` | 200 · `trace-process-001` |
| 첫 API 종료 | OS signal | lifecycle 완료 · exit 0 |
| 동일 port 재기동 | live/ready/session | 200/200/200 |
| 두 번째 API 종료 | OS signal | lifecycle 완료 · exit 0 |
| 최종 잔류 | owned process/listener | 0/0 |

Direct와 BFF의 Session `data` 의미는 동일하다. GUI browser는 열지 않았고 실제 Production Browser 검증을 주장하지 않는다.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| Runtime HTTP | 10/10 PASS |
| BFF unit | 3/3 PASS |
| 실제 API·Next production process | PASS |
| Next production build | PASS |
| Audit | 13/13 PASS |
| Identity | 18/18 PASS |
| Authorization | 22/22 PASS |
| Workspace | 34/34 PASS |
| OpenAPI | 44 path · 67 operation · 53 schema · 22 error PASS |
| Independence | 157 file · violation 0 |
| Toolchain | PASS |
| Quality runner unit | 27/27 PASS |
| Ruff·Node syntax | PASS |
| 전체 Quality Gate 선행 재실행 | 7 Category PASS · failure 0 |
| 최종 변경 후 Quality Gate | Desktop unit 1 transient 외 6 Category PASS |
| Desktop isolated 재실행 | JavaScript 25 + Rust 17 PASS |

Strict Mypy는 import된 기존 Audit·Identity·Authorization baseline 40건과 신규 2건을 함께 보고했다. 신규 Runtime 반환 및 Main signal frame 2건은 교정했다. 기존 Core baseline은 이 Work Order에서 수정하지 않았다.

## Evidence

- `docs/03_evidence/release_1/R1-M4-05/runtime-process-summary.json`
- `docs/03_evidence/release_1/R1-M4-05/bff-network-summary.json`
- `docs/03_evidence/release_1/R1-M1-05/quality-gate-result.json`
- `docs/03_evidence/release_1/R1-M1-05/quality-gate-summary.md`

## 제외 범위·남은 위험

- PostgreSQL·RLS·Migration, Docker·ysna-server, 외부 OIDC Provider, 운영 HTTPS 인증서·Reverse Proxy, M5 이후 Route는 제외했다.
- Native 실제 device HTTPS 호출과 실제 Production Browser Network는 이번 자동 process 검증 범위가 아니다.
- Local full Gate의 Desktop transient는 isolated PASS로 분리했다. GitHub CI clean Gate가 최종 운영 판단 증거다.
- PR·CI·Merge·외부 배포는 수행하지 않았다.

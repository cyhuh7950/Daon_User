COMPLETED | R1-M2-06-I001 | 계정·조직·정책·장치·Step-up·현재 권한 재검증 Production-bound Prototype 구현 완료 | Account/Organization Route, 공용 Domain·Pane·Style, 전용 Test, Adapter 계약, Browser 증거·진행 기록 | 전용 16/16·전체 회귀 93/93·Lint·Production Build·공통 Gate·네 폭 실제 Browser PASS | 실제 Auth/API/DB/MFA/Session·Key 철회/Egress는 승인 계획대로 M3~M8 후속이며 Resource Timing은 Browser 평가 Context에서 unavailable | 어울1 읽기 전용 독립 검토 후 Commit·Push·S10 검증 판단

# R1-M2-06 개발 결과보고 — Attempt 1

## 판정

`COMPLETED` · `HANDOFF_READY`

## 판단 이유

- 설계 역할 7종과 세부 권한 8종, NavigationPersona와 MembershipRole 분리, 독립 Grant/Revoke 및 Tenant·역할 상승 부정 경로를 순수 Domain 계약으로 고정했다.
- Account·Organization 실제 Next Route가 M2-01 Screen ID를 소비하며 정책 잠금, 안전한 Provider 표시, 장치 철회 Preview, Step-up, 과거 결과 현재 권한 재검증, 새 Rerun Preview, 영역 이동 5단계와 Append-only Audit를 클릭 가능하게 연결했다.
- 실제 Auth·API·DB·MFA·Session/Sync Key 철회·Egress·재색인을 수행하거나 성공으로 표시하지 않았다. `403`은 계약 Preview임을 화면에 명시했다.
- 전용 Test, 기존 M2/Foundation 회귀, Lint, Production Build, 공통 품질 Gate와 네 폭 실제 Browser 검증이 모두 통과했다.

## 변경 결과

| 구분 | 파일 |
| --- | --- |
| Domain | `packages/ui/src/account-security-model.js` |
| UI | `packages/ui/src/account-security-pane.jsx`, `packages/ui/src/index.js`, `packages/ui/src/workspace.css` |
| Route | `apps/web/app/settings/account/page.jsx`, `apps/web/app/settings/organization/page.jsx` |
| Test | `scripts/tests/account-security.test.mjs` |
| Architecture | `docs/01_architecture/account_security_prototype_adapter_contract.md` |
| Evidence | `docs/03_evidence/release_1/R1-M2-06/` |
| Recovery record | `docs/04_test_reports/release_1/R1-M2-06_progress.md` |

보호 Dirty인 `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`과 `violations.json`은 수정·복원·Stage하지 않았다. 공통 Gate가 갱신한 기존 M1-05 결과 두 파일은 Gate 실행 전 Commit 상태로 되돌려 작업 범위에 포함하지 않았다.

## 검증 근거

| 검증 | 결과 |
| --- | --- |
| 유효 RED | 최초 전용 Test 0 PASS / 14 FAIL; 미구현 계약·Route 부재로 실패 |
| 전용 Test | `node --test scripts/tests/account-security.test.mjs` · 16/16 PASS |
| 전체 회귀 | Account + Studio + Workspace + Source + Run + Foundation · 93/93 PASS |
| Lint | `npm run lint:workspace` · 11 files PASS |
| Production Build | `npm run build --workspace @daon-user/web` · Exit 0; 두 Settings Route 생성 |
| 공통 Gate | `npm run verify:quality-gate` · Overall PASS, Exit 0, 17 checks, failures 0 |
| Diff | `git diff --check` · 오류 0 |
| Browser | 새 Production Build/Session, 1920×1080·1200×900·800×900·500×900 실제 클릭, Console warning/error 0/0, 상태 초기화 0 |
| 시각 검수 | PNG 4개 직접 검수; 문서 수준 잘림·겹침 0. 500px Matrix는 명시적 내부 가로 Scroll Region |

Resource Timing은 Browser 평가 Context에서 제공되지 않아 요청 수를 0으로 추정하지 않고 `unavailable`과 사유를 Browser JSON에 기록했다. 정적 Test와 집중 Source Scan에서는 절대 API URL, `localhost`, Docker Host, `NEXT_PUBLIC_API_BASE_URL`, Browser `fetch`가 발견되지 않았다.

## 남은 위험과 후속 책임

- M2-06은 `prototype_fixture`다. 실제 OIDC/PKCE·MFA·API 401/403·RLS·Session/Sync Key·Secure Store·Egress·Copy·Version·Index는 M3~M8에서 Adapter로 교체하고 서버/DB/Network 기준으로 검증해야 한다.
- 500px의 8열 권한 Matrix는 정보 손실을 피하기 위해 내부 가로 Scroll을 사용한다. 문서 전체 Overflow는 없으며 Keyboard Scroll이 가능하다.
- Commit·Push·GitHub·ysna-server 검증은 권한 경계상 수행하지 않았다.

## 조치

어울1이 S9 읽기 전용 독립 검토를 수행한다. 합격 시 허용 파일만 Commit·Push하고, S10에서 동일 SHA의 GitHub·ysna-server ARM64 검증과 기존 자원 불변을 확인한다.

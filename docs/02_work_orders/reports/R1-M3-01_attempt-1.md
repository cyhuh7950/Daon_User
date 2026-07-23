COMPLETED | R1-M3-01-I001 | Production standalone Web Shell·same-origin BFF·상태/실패 UI 구현 | Web/UI/Test/Contract/Evidence/Progress | 전용 6/6·전체 192/192·Lint·Toolchain·Independence·Build·Quality Gate·Chrome·Lifecycle PASS | 실제 Downstream은 deferred_actual, R1-D022 Next canary 유지 | 어울1의 독립 검토와 다음 Gate 판단 요청

# R1-M3-01 Attempt 1 결과보고

## 판정

COMPLETED. 승인된 M2 자산을 변경하지 않고 실제 Next Production standalone Process가 소유하는 Web Shell로 연결했으며, 필수 자동·Chrome·Process 수명주기 증거를 생성했다.

## 수행한 작업

- Server-only Runtime Descriptor와 `/bff/shell/runtime` Route Handler를 구현했다.
- Browser는 상대 경로 1건만 요청하며 GET 200/no-store, 비허용 Method 405 안전 응답을 제공한다.
- 공통 작은 Shell 상태 표식, `i` Popover, Keyboard/Escape/ARIA, 실패·재시도 상태를 연결했다.
- `ready`를 Next Process/BFF 응답 가능 의미로 제한하고 Downstream은 항상 `deferred_actual`로 분리했다.
- 운영 제품 Metadata를 적용하고 기존 Navigation, Screen, Token, M2 Model/Reducer의 Hash 불변을 Test로 고정했다.
- Contract, Browser/Runtime/Lifecycle JSON, 4개 폭 PNG, 실패 PNG, SHA-256 Manifest를 생성했다.

## 변경 및 영향 범위

- 제품: `apps/web/app/layout.jsx`, `apps/web/app/bff/shell/runtime/route.js`, `apps/web/lib/web-shell-runtime.js`
- UI: `packages/ui/src/index.js`, `web-shell-runtime-model.js`, `web-shell-runtime-status.jsx`, `web-shell-runtime.css`
- 검증: `scripts/tests/web-runtime-shell.test.mjs`
- 문서·증거: Contract, 지정 Progress, 본 보고서, `docs/03_evidence/release_1/R1-M3-01/`
- Dependency, Lockfile, Toolchain, CI, M2 정본, Windows/Android/iOS Shell 변경 없음.

## 테스트 근거

- TDD RED: 미구현 `apps/web/lib/web-shell-runtime.js`의 `ERR_MODULE_NOT_FOUND` 확인.
- 전용 Test: 6/6 PASS.
- 전체 순차 회귀: 192/192 PASS.
- Workspace Lint: 11 files PASS.
- Toolchain: 7 npm manifests exact pins PASS.
- Independence: 52 files, violations 0.
- Fresh Production Build: PASS, 8 routes, `/bff/shell/runtime` dynamic.
- 공통 Quality Gate: 7 categories, failures 0.
- `git diff --check`: PASS; Lockfile Diff 0; 최종 4179/4180 Listener 0.

## 실제 Chrome 검증

- Home, Workspace, Account, Organization, Operations, Notifications 클릭과 Back/Forward PASS.
- 1920×1080, 1200×900, 800×900, 500×900에서 상태 보존과 가로 Overflow 0.
- 상태 Popover Enter/Open, Escape/Close, `aria-expanded`·`aria-controls` PASS.
- Page Assets 9건은 정적 자산 8건과 BFF fetch 1건이며 모두 same-origin, Cross-origin 0.
- 제한된 Chrome evaluate에서 Resource Timing API를 얻지 못해 0으로 기록하지 않고 unavailable 사유를 남겼다.
- 깨끗한 새 Chrome 세션의 Console warning/error 0. 재사용 M2 세션 탭에서 기존 Projection 상태 복원 시 React hydration 오류 1건을 관찰했으나 새 세션에서 재현되지 않았고 이번 Shell의 ready 첫 렌더는 정상이다.
- 실제 Client fetch 503 실패 화면은 `unavailable`, `ready=false`, 재시도 노출·클릭 후에도 성공을 추론하지 않았다.

## Process 수명주기

- 최초 standalone PID 20184 Ready와 BFF 200 확인 후 PID·4179 Port·자식 Process 0.
- 동일 Build PID 62996 재기동 후 Workspace 실제 클릭, Shell ready, BFF 200, Console 0 확인.
- 최종 PID 62996·자식 88116·Port 4179 모두 0; 실패 검증 PID 91200·Port 4180 모두 0.
- Windows 제어 종료는 `Stop-Process`와 `Wait-Process`를 사용했으며 Process Handle을 명령 간 유지하지 않아 Exit Code는 unavailable로 정직하게 기록했다.

## 미해결 사항과 다음 판단

- Backend, DB, LLM, Source, Delivery는 승인대로 `deferred_actual`; 외부 효과 0건, DB Migration N/A.
- 승인된 exact Toolchain의 Next `16.3.0-canary.93`과 R1-D022는 완화하지 않았다.
- 재사용 M2 Session Projection의 기존 hydration 관찰을 후속 경미 보완 후보로 분리할지 어울1이 판단해야 한다. R1-M3-01 Shell 완료를 다시 열 사유로 보지는 않는다.
- Commit, Push, PR, 서버 배포는 수행하지 않았다.

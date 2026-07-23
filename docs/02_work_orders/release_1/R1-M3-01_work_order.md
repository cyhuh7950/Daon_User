# R1-M3-01 작업지시서 — Web 실행 Shell

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M3-01` |
| issue_id | `R1-M3-01-I001` |
| 작업 | 승인된 M2 UX를 실제 Next.js Production Process와 same-origin BFF Shell 경계에서 실행 |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| Branch/Worktree | `codex/r1-m3-01` · `C:\tmp\Daon_User-r1-m3-01` |
| 기준 SHA | `d1e86e5d5ece6ba41975c84d3bf4562d7c2f3de3` |
| 선행 Gate | `G2-UX GO` · `APR-G2-UX-20260723-01` |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-01_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-01_attempt-1.md` |
| 후속 Gate | M3 Exit 이후 TP-3 전까지 개별 사용자 Gate 없음. 범위·API·데이터·보안 변경은 즉시 어울1 회부 |

어울2는 착수 전에 아래 정본을 EOF까지 읽고 SHA-256을 대조한다. 요약본으로 대체하지 않는다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| `docs/04_test_reports/release_1_test_plan.md` | `359404A190D248E94F2BE4A69CB285D10422FA426C32D4C5409F868F4CA4768B` |
| `docs/01_architecture/production_bound_prototype_handoff_contract.md` | `A04CCF36992D0913F97C997B023805C13633E12AA3DD4C6309B799531671324D` |
| `docs/04_test_reports/release_1/wave_TP-1.md` | `4C531341762E3B790C6BE065A166EB38771CA511401C2715727426EF65F8F0F5` |
| `docs/04_test_reports/release_1/approval_G2-UX.md` | `007D5927A9D90291F7A190FF56D46B44B5CE58861758C3C25F27B43F4C605583` |
| `docs/01_architecture/DECISIONS.md` | `6BEC74CB940B8F1DB19A3800AEFEA507D04CD5F777EC22BC4EE7185242E36227` |
| `docs/03_evidence/release_1/R1-M2-08/evidence-manifest.json` | `D776F1A4EAF223AA0BD6A90EB2BBC362CB9F35980689C58092BD400DDD038F9E` |

## 2. 목적과 사용자 관점 완료 조건

R1-M3-01은 M2 화면을 새로 설계하는 작업이 아니다. 승인된 IA·Route·Screen·Token·Workspace State·접근성·반응형 자산을 폐기하지 않고 실제 Web Production Process가 소유하는 실행 Shell로 승격한다.

사용자는 다음을 확인할 수 있어야 한다.

1. Production Build로 시작한 실제 Web Process에서 Home·Workspace·Account·Organization·Operations·Notifications Route를 클릭한다.
2. Web Shell 상태를 화면의 작은 상태 표식과 Tooltip/Popover로 확인한다. 상시 설명 박스는 사용하지 않는다.
3. Browser가 Web Shell 상태를 same-origin 상대 경로로 조회한다. 내부 API 주소·Docker Host·`localhost`·Provider URL은 Browser Source·DOM·Network에 없다.
4. Web Process를 정상 종료하면 지정 Port와 자식 Process가 남지 않고, 같은 Build를 재기동하면 핵심 Route가 다시 동작한다.
5. 실제 Backend·DB·LLM·파일·전달 Adapter가 없는 기능은 계속 `deferred_actual` 또는 `unavailable`이며 성공으로 표시되지 않는다.

## 3. 설계 계약

### 3.1 Production Web Process

- `apps/web`의 기존 Next `output: standalone`과 승인된 exact Toolchain을 유지한다.
- Fresh Production Build와 Production Start를 사용한다. Dev Server 결과를 완료 증거로 사용하지 않는다.
- Process 식별자, 시작·준비·종료 시각, 종료 Code, Listen Address/Port, 재기동 결과를 기계 판독 Evidence로 남긴다.
- 검증용 Process는 종료 직전 반드시 정상 종료하고 지정 Port·자식 Process 잔존 0건을 확인한다.
- 화면 Metadata에서 M2 Prototype을 운영 제품으로 오인시키는 표현은 Web Shell 범위 안에서 정리하되, 미구현 기능의 Mock/Projection 표시는 유지한다.

### 3.2 same-origin BFF Shell 경계

- Browser는 단일 same-origin 상대 경로로 Web Shell Runtime 상태만 조회한다. 권고 경로는 `/bff/shell/runtime`이며 기존 Route와 충돌하면 증거를 제출하고 어울1 판단을 받는다.
- Route Handler 또는 동등한 Server 경계 뒤에 Server-only Runtime Descriptor를 둔다. Browser Component가 내부 주소나 Server 설정을 Import하지 않는다.
- 이 응답의 `ready`는 Next Process와 BFF Shell 경계가 응답 가능하다는 뜻만 가진다. Backend·DB·LLM·Source·Delivery 준비 완료를 뜻하지 않는다.
- 응답은 안정 Code, Shell Version/Build 식별, `downstream_state=deferred_actual`과 공개 가능한 시각만 포함한다. Secret·환경변수 값·내부 Host/Port·Stack Trace·Raw 오류는 포함하지 않는다.
- M3-01에서 실제 Downstream Network 호출, 인증·Tenant·공개 Business API를 구현하지 않는다. 이는 M4 이후 범위다.
- 허용되지 않은 Method는 안전한 상태 Code로 거부하고, 오류 응답도 내부 정보를 노출하지 않는다.

### 3.3 승인 UX 승계

- `navigation.json`, `screens.json`, `product_sitemap.md`, Design Token과 M2 State/Reducer를 직접 소비한다.
- Home Evidence Hub를 포함한 기존 M2 Route·Pane·Drawer·Tooltip·오류 상태를 삭제하거나 재작성하지 않는다.
- 기준 화면 1920×1080, 본문/폼 12px, 작은 설명 10px, 보조 9px, 사이드바 제목 14px, 제목 16px를 유지한다.
- 1920/1200/800/500 네 폭에서 Route 왕복·선택·Pane·Evidence 위치와 가로 Overflow 0을 재검증한다.
- `client_type`, 화면 폭, NavigationPersona로 MembershipRole·Capability를 만들지 않는다.

### 3.4 실패 정직성

- Runtime 조회가 실패하면 마지막 성공 상태를 유지한 채 성공처럼 표시하지 않는다. 사용자가 재시도할 수 있는 안전 상태를 제공한다.
- Process가 재기동 중이면 `starting/recovering`과 같은 Shell 상태로 표시하고 실제 Downstream 성공을 추론하지 않는다.
- Browser Network/Resource Timing을 얻지 못하면 0건으로 기록하지 말고 `unavailable` 사유를 남긴다.

## 4. 허용·금지 변경

허용:

- `apps/web/`의 Production Shell·Route Handler·Server-only Runtime 경계·Metadata·최소 상태 UI
- 필요한 경우 `packages/ui/src/`의 Web Shell 전용 작은 상태 Component와 Export·CSS
- `scripts/tests/web-runtime-shell.test.mjs` 또는 동등한 단일 전용 Test
- `docs/01_architecture/web_runtime_shell_contract.md`
- `docs/03_evidence/release_1/R1-M3-01/`
- 지정 Progress·결과보고

금지:

- `packages/contracts/navigation.json`, `screens.json`, Design Token 정본 변경
- Dependency·Lockfile·Toolchain·CI 변경과 `R1-D022` 완화
- M2 Model·Reducer·화면 전체 재작성, 무관 Refactor, 전체 코드 재작성
- 실제 Backend·DB·Migration·Auth·Tenant·Queue·LLM·File·Export·Delivery 구현 또는 외부 효과
- Browser 코드의 절대 API 주소, `localhost`, `127.0.0.1`, Docker Host/Port, `NEXT_PUBLIC_API_BASE_URL`
- Server 내부 주소·Secret·Raw 오류·Chain-of-Thought의 Browser/DOM/Console/Evidence 노출
- Windows·Android·iOS Shell 변경

실제 코드가 이 허용 범위와 충돌하면 증거를 Progress에 남기고 구현을 확대하지 않은 채 어울1에게 회부한다.

## 5. TDD·작업 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| S0 | 정본 EOF·Hash, G2 승인, 기준 SHA, Branch, 단일 Writer, Diff 0 확인 | Progress |
| S1 | 기존 Web 실행 구조·M2 승계 자산·요청 주체·실제 URL·회귀 영향 분석 | 영향 Matrix |
| S2 | Production Shell·BFF 상대 경로·정보 비노출·실패 정직성 Test 선작성 | 유효 RED |
| S3 | Server-only Runtime Descriptor와 same-origin Route Handler 최소 구현 | 전용 Test Green |
| S4 | 승인 UX에 작은 상태 Interface 연결, 실패·재시도 상태 구현 | UI Test Green |
| S5 | 기존 전체 회귀·Lint·Toolchain·독립성·Production Build·공통 Gate | 전부 PASS |
| S6 | 실제 Production Process 시작·Chrome 네 폭·Route·Network·Console 검증 | Browser JSON·PNG |
| S7 | 정상 종료·Port/자식 Process 0·동일 Build 재기동·재클릭 | Lifecycle JSON |
| S8 | Contract·Evidence Manifest·결과보고·Diff 최종화 | 정식 결과 상태 |

각 단계에서 착수, 세부 단계 완료, 오류·원인·복구, 테스트 완료, 종료 직전에 Progress를 갱신한다. 필수 필드는 시각·단계·상태·변경 파일·명령/테스트 결과·오류/원인/복구·다음 작업이다. Windows 파일 잠금은 관련 Process 생존을 확인하며 충분히 기다리고, 같은 정리 명령을 근거 없이 반복하지 않는다.

## 6. 필수 검증

자동 검증:

- BFF 경계가 Browser에서 same-origin 상대 경로만 사용
- Runtime Descriptor와 Route Handler가 Server 경계에 있고 내부 주소·Secret 비노출
- `ready`와 `downstream_state=deferred_actual` 의미 분리
- 허용 Method와 안전 오류 응답
- 승인 Navigation·Screen·Token·M2 Model 불변
- 전용 Test, 전체 순차 회귀, Workspace Lint, Toolchain, Independence, Production Build, 공통 7범주 Quality Gate
- `git diff --check`, 관련 없는 변경·추적 삭제·Lockfile Diff 0

Production Browser:

- Fresh Build를 실제 Production Process로 실행
- Home·Workspace·Account·Organization·Operations·Notifications 실제 클릭과 Browser Back/Forward
- Web Shell 상태 Interface와 실패·재시도 상태 확인
- 1920×1080, 1200×900, 800×900, 500×900에서 상태 보존·가로 Overflow 0
- Keyboard/Focus/Tooltip/Escape/ARIA
- Network 요청 URL이 same-origin이며 내부주소·비동일 Origin·의도하지 않은 API 요청 0
- Console warning/error 0
- 종료 후 Process·Port 0, 동일 Build 재기동 후 핵심 Route와 BFF 상태 재확인

Evidence:

- `docs/03_evidence/release_1/R1-M3-01/web-shell-runtime.json`
- `browser-validation.json`, `process-lifecycle.json`
- 네 폭 핵심 PNG와 Runtime 실패 상태 PNG
- `evidence-manifest.json`: Artifact SHA-256·Byte, 대상 Commit/환경, 실제/Mock/Deferred 경계
- 실제 외부 효과 0건과 DB Migration N/A 명시

## 7. 결과보고·상태 판정

결과보고 첫 줄:

```text
COMPLETED | R1-M3-01-I001 | 수행 요약 | 변경 파일 | 테스트 근거 | 미해결 위험 | 어울1 검토 요청
```

- `COMPLETED`: 위 산출물과 자동·Browser·Lifecycle 증거가 모두 있다.
- `FAILURE_REPORT`: 동일 issue_id, 실패 단계·원인·오류·관련 코드·현재 변경·남은 작업·필요 판단을 포함한다.
- `INCOMPLETE`: 예기치 않은 중단 또는 결과보고 미완성이다. 현재 상태부터 이어갈 수 있게 Progress를 남긴다.
- `BLOCKED`: 권한·환경·승인 경계로 진행할 수 없으며 필요한 결정만 구체적으로 적는다.

중대 미진은 별도 수정 작업지시 대상으로 보고한다. 합격 가능한 경미 보완은 다음 작업에 흡수할 수 있게 구분한다. 사소한 이유로 합격 작업 전체를 다시 열지 않는다.

어울2는 Commit·Push·PR·Merge·ysna-server 배포를 수행하지 않는다. 완료 후 추가 쓰기를 중지하고 어울1에게 제출한다.

# R1-M3-04 작업지시서 — React Native 공용 Shell

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M3-04` |
| issue_id | `R1-M3-04-I001` |
| 작업 | Android·iOS가 함께 사용하는 React Native 공용 Shell의 Navigation·Design Token·Domain/OpenAPI Client 경계 구현 |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| Branch/Worktree | `codex/r1-m3-04` · `C:\tmp\Daon_User-r1-m3-04` |
| 기준 SHA | `5c13826fa641d5d8699e01da543c3c96a4b854ab` |
| 선행 Gate | `G2-UX GO` · `APR-G2-UX-20260723-01` |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-04_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-04_attempt-1.md` |
| 후속 작업 | `R1-M3-05` Android 설치 Shell, `R1-M3-06` iOS 설치 Shell |
| 후속 Gate | M3 Exit·CP2 이후 TP-3 전까지 개별 사용자 Gate 없음. 범위·공개 API·데이터·보안 경계 변경은 즉시 어울1 회부 |

어울2는 착수 전에 `AGENTS.md`와 아래 정본을 EOF까지 읽고 SHA-256을 대조한다. 요약본으로 대체하지 않는다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| `docs/04_test_reports/release_1_test_plan.md` | `359404A190D248E94F2BE4A69CB285D10422FA426C32D4C5409F868F4CA4768B` |
| `docs/04_test_reports/release_1/approval_G2-UX.md` | `007D5927A9D90291F7A190FF56D46B44B5CE58861758C3C25F27B43F4C605583` |
| `docs/01_architecture/DECISIONS.md` | `6BEC74CB940B8F1DB19A3800AEFEA507D04CD5F777EC22BC4EE7185242E36227` |
| `docs/01_architecture/toolchain_dependency_baseline.md` | `28963DA75A4B7EE3A0CDF06F03785A9346BF62FBBCB8FFE212792960D454408D` |
| `docs/01_architecture/monorepo_ownership_boundaries.md` | `CCBBF74E5C7EDE1B96061B649F08DCFD82F7446023AB0117C08A51716E4E753C` |
| `docs/01_architecture/production_bound_prototype_handoff_contract.md` | `A04CCF36992D0913F97C997B023805C13633E12AA3DD4C6309B799531671324D` |
| `docs/03_evidence/release_1/R1-M2-08/evidence-manifest.json` | `AF37E617342A9836138E6B748F19B7C04C492EA322D6F9A8E2574FA5F2D78DAF` |

계획서 표의 G2-UX 상태 문구가 과거 `미착수`로 남은 것은 기존 문서 Drift다. 현행 승인 정본은 위 승인 기록의 `GO`이며, 이번 작업에서 계획서를 임의 수정하지 않는다.

## 2. 목적과 사용자 관점 완료 조건

R1-M3-04는 Android와 iOS용 화면을 각각 별도 구현하거나 Web DOM 화면을 재사용하는 작업이 아니다. 승인된 Route·Screen·Token·상태·모바일 업무 범위를 React Native 공용 계층으로 승계하고, 후속 Native 설치 Shell과 실제 Public API가 교체 연결할 안정 경계를 만든다.

사용자는 후속 Android/iOS App에서 동일하게 사용할 다음 기반을 얻어야 한다.

1. `android` 또는 `ios`를 명시하면 Contract에서 해당 Client에 허용된 여덟 Route만 `native_route_key`로 탐색한다.
2. Home·Workspace·Inbox·Run History·Notifications·Model Connections·Account 화면이 React Native 기본 Component와 공용 Token으로 일관되게 표현된다.
3. 각 화면은 `loading`, `empty`, `ready`, `warning`, `error`, `forbidden`, `unavailable`을 정직하게 표현하고 실제 Adapter가 없을 때 성공을 가장하지 않는다.
4. 모바일의 허용 편집·검토 작업과 Web/Windows로 이어서 해야 하는 차단 작업이 승인된 15개 Matrix와 안정 Code를 보존한다.
5. Shell Source에는 DOM UI, Web/Next Module, 내부 API 주소, Provider 주소, Secret 또는 개발용 성공 Mock이 없다.

## 3. 설계 계약

### 3.1 React Native 공용 Shell

- `apps/mobile`은 React Native `0.86.0`, React `19.2.7`, TypeScript `7.0.2` 고정 기준을 사용한다. 실제 호환성에 필요한 의존성만 정확 버전으로 추가하고 Lockfile을 갱신한다.
- `android/`, `ios/`, APK, Archive, Signing, 권한, Deep Link와 기기 Lifecycle은 각각 R1-M3-05·06 소유이므로 이번에 생성하거나 완료로 주장하지 않는다.
- 공용 Shell은 React Native 기본 Component만 사용한다. `packages/ui`, React DOM, Browser API, Next.js, Web CSS를 Import·복사하지 않는다.
- Android/iOS 공용 Source와 Platform Adapter 경계를 분리하되 동일 화면을 플랫폼별로 복제하지 않는다.
- 실제 Native Navigation Library 도입은 필요성이 Test로 입증될 때만 허용한다. 도입하지 않으면 안정적인 순수 Navigation State와 Host Adapter Interface로 후속 M3-05·06이 연결할 수 있어야 한다.

### 3.2 Navigation·Screen Projection

- `packages/contracts/navigation.json`과 `screens.json`을 정본으로 직접 소비하며 Route·Screen 값을 별도 상수로 복제하지 않는다.
- `client_type`은 `android | ios`의 명시 입력이다. 화면 폭·OS 추론으로 바꾸지 않으며 MembershipRole·Capability·쓰기 권한을 만들지 않는다.
- `clients`에 해당 Native Client가 포함되고 `native_route_key`가 있는 Route만 노출한다. `organization_settings`, `operations`는 모바일 탐색에 나타나면 안 된다.
- Route 선택·복귀·Deep Link 입력은 허용 Route만 받는 Fail-close 경계를 제공한다. 실제 Deep Link OS 등록은 후속 작업이다.
- Screen State는 Contract의 일곱 상태만 허용하고 알 수 없는 Route·State·Adapter 결과를 `ready`로 승격하지 않는다.

### 3.3 Design Token·접근성

- `@daon-user/design-tokens`를 정본으로 소비하고 색상·간격·글꼴·상태 값을 모바일 Source에 중복 선언하지 않는다. CSS 문자열은 React Native Style 값으로 변환하는 명시적 Platform Adapter에서만 해석한다.
- 본문/폼 12px, 작은 설명 10px, 보조 9px, Sidebar 제목 14px, 제목 16px 기준과 Touch 우선 44px Target을 Token 기준으로 보존한다.
- OS 글꼴 확대, Screen Reader Label, Focus/선택 상태, 상태별 색상 외 텍스트·Icon 신호를 제공한다.
- 상시 설명 박스를 만들지 않는다. 모바일 설명은 접근 가능한 Info Action·Popover/Modal Adapter 경계로 제공하며 실제 Host 연결 전에는 정직한 `unavailable` 상태를 사용한다.

### 3.4 Domain·OpenAPI Client 경계

- Domain Type은 `packages/contracts`의 현재 JSON 정본에서 유도하거나 검증되는 플랫폼 중립 Type으로 둔다. 공개 API Schema 자체는 R1-M4-01 소유이므로 이번에 임의 확정하지 않는다.
- `PublicApiClient` 경계는 인증된 HTTPS Public Gateway를 후속 주입할 Interface와 안정적 결과/오류 Mapping까지만 제공한다. Base URL·Auth Token 저장·실제 HTTP·Refresh·Tenant·권한 구현은 하지 않는다.
- Adapter가 없으면 Screen은 `unavailable`과 교체 Owner를 표시한다. Fixture는 순수 Projection/Test 입력으로만 사용하고 실제 연결 성공으로 표시하지 않는다.
- Browser용 same-origin BFF, Tauri IPC/Loopback, Docker 내부 Host/Port를 Native Client 경계에 혼합하지 않는다.
- `fetch`, `localhost`, `127.0.0.1`, Docker Host, 고정 Server URL, `NEXT_PUBLIC_API_BASE_URL`, Daon 내부 경로, API Key·Secret을 Client Source에 넣지 않는다.

### 3.5 모바일 Studio Allowlist 승계

- 승인된 15개 Matrix는 M2 Domain 정본과 설계 §4.2.1의 의미를 변경하지 않는다: 허용 Content 3개, 허용 비Content 6개, 차단 6개다.
- 허용 Content는 제목·기존 텍스트·기존 단순 표 Cell 값 수정이고 새 Revision이 필요하다. 구조·Layout·Section·표 구조·근거 연결·생성 설정·전체 재생성은 차단한다.
- Comment·수정 요청·검토·승인·반려·알림·Citation/Source 읽기 범위는 기존 Domain 결과와 안전 Code를 보존한다.
- 이번 Client 검사는 UX 경계이며 보안 강제가 아니다. M4 Native Gateway가 같은 Allowlist를 서버에서 재강제하기 전까지 권한 검증 완료로 주장하지 않는다.
- DOM 전용 `packages/ui`의 구현을 Import하지 않고, 필요한 Matrix는 승인 정본에서 생성·검증되는 모바일용 플랫폼 중립 계약으로 승계한다. 값의 중복 정본을 만들지 않는다.

## 4. 허용·금지 변경

허용:

- `apps/mobile/` 공용 TypeScript Source, Entry, Navigation/Screen/State/Token/Adapter 경계, Test와 Build 설정
- 모바일 Shell 검증용 `scripts/`와 Root Script, `quality-gate-policy.json`의 실제 Mobile Capability 활성화
- 필요한 정확 버전의 `apps/mobile/package.json`, Root `package.json`, `package-lock.json`
- 공개 Contract 값을 바꾸지 않는 `packages/contracts`의 플랫폼 중립 Type/검증 Export 최소 추가
- Token 값을 바꾸지 않는 `packages/design-tokens`의 모바일 소비 경계 최소 보완
- `repo-boundaries.json`의 기존 Mobile 경계를 강화하는 기계 검증
- `docs/01_architecture/react_native_shared_shell_contract.md`
- `docs/03_evidence/release_1/R1-M3-04/`, 지정 Progress·결과보고

금지:

- 승인된 Navigation·Screen·Design Token·모바일 Allowlist 값·의미 변경
- `packages/ui`, `apps/web`, `apps/desktop`, `services/api`, `services/local-service` 변경 또는 Import
- Android/iOS Native Project, APK·IPA·Archive·Signing·Device/Simulator 성공 주장
- 실제 OpenAPI v1 Schema, Auth·Tenant·권한·Audit·Gateway·Network·DB·Migration·LLM·File·Sync 구현
- DOM/React DOM/Next/Web CSS 복사, 플랫폼별 화면 복제, 무관 Refactor·전체 재작성
- 내부/고정 API URL, Browser BFF·Tauri Loopback 혼합, `fetch` 기반 임시 Client, Secret·Token 저장
- Production 성공 경로의 Stub Server·Mock 성공, 개발 편의용 임시 구조
- Toolchain Pin 완화, 범위 외 Upgrade, CI 정책 변경
- Commit·Push·PR·Merge·ysna-server 배포

실제 코드와 계약이 충돌하면 Progress에 경로·근거·대안을 기록하고 범위를 확대하지 않은 채 어울1에게 회부한다.

## 5. TDD·작업 단계

모든 Production 동작은 실패하는 Test를 먼저 작성하고 기대한 이유의 RED를 확인한 뒤 최소 구현으로 GREEN을 만든다.

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| S0 | 정본 EOF·Hash, G2 승인, 기준 SHA, Branch, 단일 Writer, 깨끗한 시작 Diff 확인 | Progress |
| S1 | 기존 Mobile Scaffold·Contract·Token·M2 Allowlist·Quality Gate·의존성 영향 분석 | 영향 Matrix |
| S2 | Native Route Projection·일곱 상태·DOM 격리·Token 소비·Client 경계·Allowlist Test 선작성 | 유효 RED |
| S3 | 플랫폼 중립 Navigation/Domain/OpenAPI Client Boundary 최소 구현 | Unit/Contract GREEN |
| S4 | React Native 공용 Shell·접근성·상태 표현 최소 구현 | Component/Type GREEN |
| S5 | 실제 React Native Bundler가 공용 Entry를 해석하는 Headless Bundle Smoke와 Quality Gate Capability 활성화 | Bundle·Gate PASS |
| S6 | 전체 회귀·Lint·Type·Unit·Contract·Build·Security·Independence·공통 7범주 Gate | 전부 PASS |
| S7 | Architecture·Evidence·Manifest·Progress·결과보고·Diff 최종화 | 정식 결과 상태 |

각 단계의 착수·완료, 오류 발생·원인·복구, 테스트 완료, 종료 직전에 Progress를 갱신한다. 필수 필드는 시각·단계·상태·변경 파일·명령/테스트 결과·오류/원인/복구·다음 작업이다.

Build·npm·TypeScript·Metro 명령은 충분히 기다린다. Windows 파일 잠금은 Process와 대상 경로를 확인하고 같은 정리 시도를 근거 없이 반복하지 않는다. GUI·Emulator·Simulator는 열지 않는다.

## 6. 필수 검증

자동 검증:

- Android/iOS 각각 허용 Route 8개, 동일 순서·안정 `native_route_key`, 조직 설정·운영 Route 0개
- 알 수 없는 Client·Route·State·Deep Link 거부와 `ready` 성공 가장 0건
- Navigation/Screen JSON 정본 직접 소비, 공용 Type·Token 사용, Contract/Token 값 수동 복제 0건
- DOM UI·React DOM·Next·Web CSS·Browser API·`packages/ui` Import 0건
- 일곱 Screen State, 접근 가능한 Label, OS 글꼴 확대, 44px Touch Target과 상태의 비색상 신호
- Domain/OpenAPI Client Interface의 `unavailable` 기본값, 실제 HTTP·Base URL·Auth·Tenant 구현 0건
- 모바일 Studio 15개 Matrix의 허용/차단·Revision·안전 Code·Web/Windows 이어서 작업 의미 보존
- React Native Entry Type Check와 Headless Production Bundle Smoke. Android/iOS Native Build는 `Deferred R1-M3-05/06`으로 정확히 표시
- Workspace Lint, 전용 Unit/Contract, Toolchain, 전체 선행 회귀, Security/Audit, Independence, 공통 7범주 Quality Gate
- `git diff --check`, 승인 정본 Diff 0, 관련 없는 변경·추적 삭제 0, Lockfile Diff는 실제 정확 Pin에 한정

Evidence:

- `docs/03_evidence/release_1/R1-M3-04/mobile-shell-contract.json`
- `mobile-shell-build.json`, `mobile-security-boundary.json`
- `quality-gate-result.json`, `quality-gate-summary.md`
- `evidence-manifest.json`: Source/Evidence Hash·Byte, 환경, 명령·Exit Code, 실제/자동/Deferred 경계
- DB Migration `N/A`, ysna-server·Git·PR `어울1 후속`, Android/iOS Native Build·Device·Simulator `Deferred R1-M3-05/06`, Public API·Auth `Deferred M4` 명시

생성 Bundle·Cache·`node_modules`는 Git에 넣지 않는다. 결과보고 전 Source Manifest와 Evidence Manifest의 Hash·Byte를 실제 파일에서 독립 재대조한다.

## 7. 기존 기능 보호·완료 판정

- M1~M3-03 전체 회귀와 저장소 독립성 검사를 통과해야 한다.
- 승인 정본, Web·Desktop·Local Service, 기존 UI·Contract·Token 값은 수정하지 않는다.
- 정적 검사·Type Check·Bundle 성공은 Native 설치·기기·API 성공 증거가 아니다.
- 검증하지 못한 항목을 0건 또는 PASS로 바꾸지 않고 `unavailable`, `deferred`, `BLOCKED` 사유를 기록한다.
- C2/C3 결함, 계약 값 변경, 공개 API·데이터·보안 경계 변경 필요는 완료로 보고하지 않는다.
- 기능 계약을 충족하고 경미한 문서 보완만 남으면 `COMPLETED`에 명시하며 다음 작업에 흡수할 수 있다.

## 8. 종료 결과 계약

결과보고 첫 줄과 최종 응답은 다음 필드를 모두 포함한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

- `COMPLETED`: 모든 필수 산출물과 완료 조건별 증거가 있다.
- `FAILURE_REPORT`: 동일 문제 issue_id, 실패 단계·확인 원인·오류/테스트/코드 증거·현재 변경·남은 작업·필요 판단이 모두 있다.
- `INCOMPLETE`: 예기치 않은 중단 또는 결과·증거가 불완전하다.
- `BLOCKED`: 권한·환경·승인 결정이 필요하다.

형식만 채우지 말고 실제 Diff·명령·Exit Code·Hash·Byte를 제출한다. `Done`은 완료 증거가 아니다.

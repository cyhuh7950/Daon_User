# R1-M3-02 작업지시서 — Windows Tauri Shell

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M3-02` |
| issue_id | `R1-M3-02-I001` |
| 작업 | 승인된 M2 UX를 공용 React UI 기반 Tauri 2 Windows 설치 App과 실제 Window 수명주기로 승격 |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| Branch/Worktree | `codex/r1-m3-02` · `C:\tmp\Daon_User-r1-m3-02` |
| 기준 SHA | `77549fb8fe86e4a651f83b13f9ec26941d09c31d` |
| 선행 Gate | `G2-UX GO` · `APR-G2-UX-20260723-01` |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-02_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-02_attempt-1.md` |
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
| `docs/01_architecture/toolchain_dependency_baseline.md` | `28963DA75A4B7EE3A0CDF06F03785A9346BF62FBBCB8FFE212792960D454408D` |
| `docs/01_architecture/monorepo_ownership_boundaries.md` | `285CBF24C5F4FE1F28091608A0D260406B106887EE1CF0539EC29FA28CC66D32` |

## 2. 목적과 사용자 관점 완료 조건

R1-M3-02는 Windows 화면을 새로 설계하거나 Web App을 URL로 감싸는 작업이 아니다. 승인된 IA·Route·Screen·Token·Workspace State·접근성·반응형 React UI를 실제 Tauri 2 Windows Process가 소유하는 설치형 Shell로 승격한다.

사용자는 다음을 확인할 수 있어야 한다.

1. 실제 NSIS 설치 EXE로 App을 설치하고 Windows 시작 메뉴 또는 설치된 실행 파일에서 Daon 사용자 프로그램을 실행한다.
2. 설치 App에서 Home·Workspace·Account·Organization·Operations·Notifications 화면을 실제 클릭하고 Window 크기를 바꿔도 선택·Pane·Viewer 상태가 보존된다.
3. App 종료 뒤 Tauri Process·Window·App이 소유한 Port와 자식 Process가 남지 않으며, 재기동하면 핵심 화면이 다시 동작한다.
4. Production App은 원격 Web URL·Dev Server·내부 Host를 불러오지 않고 Build에 포함된 정적 React 자산만 표시한다.
5. 실제 Backend·DB·LLM·파일·Local Service·IPC·Loopback Adapter가 없는 기능은 `deferred_actual` 또는 `unavailable`이며 성공으로 표시되지 않는다.

## 3. 설계 계약

### 3.1 Tauri 2 Production Shell

- `apps/desktop`에 Rust `1.97.1`, Tauri CLI `2.11.4` 기준의 Tauri 2 App을 구성한다. JavaScript·Rust·Tauri 의존성은 정확 버전으로 Pin하고 npm·Cargo Lockfile을 함께 갱신한다.
- Production Tauri Build는 공용 React UI의 정적 Production Build를 App에 포함한다. Production Window가 `http://`, `https://`, `localhost`, `127.0.0.1`, Docker Host 또는 개발 Server를 시작하거나 탐색해서는 안 된다.
- Windows Bundle 대상은 실제 설치 가능한 NSIS EXE다. R1-M3-02의 서명 자격 증명과 Update/Rollback은 범위 밖이므로 서명되지 않은 개발 검증 Installer는 `unsigned_development`로 명시하고 서명 완료로 주장하지 않는다. 서명·Update·Rollback은 M9의 `TS-OPS-041`이 검증한다.
- Product Identifier, App Version, Window Title과 Bundle Metadata는 안정적으로 고정한다. 임시·Prototype·Mock을 제품명에 넣어 별도 제품처럼 만들지 않는다.
- App 종료는 Window와 Tauri Process를 함께 정상 종료한다. R1-M3-03 전에는 Local Service나 별도 Backend Process를 시작하지 않는다.

### 3.2 승인 UX와 공용 React UI 승계

- `packages/ui`, `packages/design-tokens`, `packages/contracts/navigation.json`, `packages/contracts/screens.json`을 직접 소비한다.
- `apps/web`의 Next Page·Route Handler·Server Module·BFF를 Import하거나 복사하지 않는다. Web과 Windows가 공유하는 것은 React UI·Token·Domain·공개 Contract다.
- Windows Native Navigation은 `native_route_key`를 안정 Key로 사용하고 `clients`에 `windows`가 허용된 Route만 노출한다. `client_type=windows`는 표시·Adapter 선택 정보일 뿐 MembershipRole·Capability를 만들지 않는다.
- Home·Workspace·Account·Organization·Operations·Notifications의 기존 M2 Component·상태·오류·복구·Tooltip 계약을 보존한다. 구현되지 않은 Route나 Action을 성공처럼 연결하지 않는다.
- 기준 화면 1920×1080, 본문/폼 12px, 작은 설명 10px, 보조 9px, Sidebar 제목 14px, 제목 16px를 유지한다. 1920×1080, 1200×900, 800×900, 500×900 Window에서 상태 보존·가로 Overflow 0·Keyboard/Focus/Tooltip/Escape/ARIA를 확인한다.

### 3.3 Native 보안 경계

- Tauri Capability·Permission은 현재 Shell에 필요한 최소 Window/Core 범위만 허용한다. Shell·Command·Filesystem·Process·HTTP·Clipboard·Updater·Notification·Deep Link Plugin은 이번 범위에서 필요하지 않으면 추가하지 않는다.
- Remote Content, 임의 Navigation, 새 Window/Popup, File Drop과 외부 URL 열기는 기본 거부한다. 필요성이 발견되면 구현하지 말고 근거와 최소 권한안을 어울1에게 회부한다.
- CSP는 Build에 포함된 자산만 허용하는 Fail-close 기준으로 둔다. `unsafe-eval`, 범용 `*`, 임의 `connect-src`를 허용하지 않는다.
- Secret·Credential·환경변수 값·내부 Host/Port·Raw 오류·Stack Trace·Chain-of-Thought를 UI·Console·Evidence·Binary 문자열에 포함하지 않는다.
- App Source·Config·Bundled Frontend에서 앱이 정의한 내부 API·Provider URL·`localhost`·`127.0.0.1`·Docker Host·`NEXT_PUBLIC_API_BASE_URL`·Daon 내부 경로 직접 의존 0건을 검사한다.
- Tauri Runtime·WebView2·공급망 Binary에 포함된 예약 내부 Origin이나 문자열은 앱 정의 Network Target과 분리한다. 허용하려면 `tauri_reserved_internal_origin` Allowlist에 정확한 값·발견 Artifact·공급망 Package/Runtime 출처·앱 설정 또는 호출 지점 0건을 기록해야 하며, 범용 문자열 제외나 앱이 정의한 Dev/API URL 면제에 사용하지 않는다.

### 3.4 R1-M3-03 경계와 실패 정직성

- Local Service 실행·감시·재시작·서명 검증, IPC Command, Loopback API, App Instance 인증, Port Allowlist는 `R1-M3-03` 소유다. 이번 작업에서는 Stub Service, 임시 HTTP Server, 개발 편의 Bridge를 만들지 않는다.
- UI는 Local Service·Cloud Gateway·Auth·DB·Model 연결을 `deferred_actual` 또는 `unavailable`로 표시한다. Tauri Window가 열렸다는 이유로 Downstream을 `ready`로 추론하지 않는다.
- Native Runtime 오류는 안정 Code와 사용자 재시도 또는 종료 선택으로 표시하며 Raw 내부 오류를 노출하지 않는다.
- Installer 생성·설치·실행·UI 검증 중 실제 증거를 얻지 못한 항목은 0건이나 PASS로 기록하지 않고 `unavailable` 또는 `BLOCKED` 사유를 남긴다.

## 4. 허용·금지 변경

허용:

- `apps/desktop/`의 React Entry·Native Navigation·Production Asset Build·Tauri `src-tauri`·최소 Capability·Bundle 설정
- 필요한 정확 버전의 `apps/desktop/package.json`, 루트 `package-lock.json`, `apps/desktop/src-tauri/Cargo.toml`, `Cargo.lock`
- 필요한 경우 `packages/ui/src/`의 플랫폼 중립 Desktop Shell용 작은 Component·Export·CSS
- 전용 검증 Script·Test와 해당 Root Script 등록
- `docs/01_architecture/windows_tauri_shell_contract.md`
- `docs/03_evidence/release_1/R1-M3-02/`
- 지정 Progress·결과보고

금지:

- Navigation·Screen·Design Token 정본 값 변경
- Rust·Node·npm·Corepack·Tauri·React·TypeScript Pin 완화, 범위 외 의존성 Upgrade, CI 정책 변경
- M2 Model·Reducer·화면 전체 재작성, Web 화면 복제, 무관 Refactor, 전체 코드 재작성
- 실제 Backend·DB·Migration·Auth·Tenant·Queue·LLM·File·Export·Delivery 구현 또는 외부 효과
- Local Service·IPC·Loopback·App Instance 인증의 임시 구현
- 원격 Web App Wrapper, Production Dev Server, Browser/Tauri Frontend의 절대 API 주소·내부 Host/Port
- 서명 Credential·Certificate·Private Key 생성·조회·저장, Windows 운영 설정 변경
- Android·iOS Shell과 `apps/web` 변경
- 생성된 EXE·MSI·Build Cache·`node_modules`·`target` Binary의 Git 추적

실제 코드나 Windows 환경이 이 허용 범위와 충돌하면 증거를 Progress에 남기고 범위를 확대하지 않은 채 어울1에게 회부한다.

## 5. TDD·작업 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| S0 | 정본 EOF·Hash, G2 승인, 기준 SHA, Branch, 단일 Writer, 작업지시 발행 Commit 이후 미커밋 Diff 0 확인 | Progress |
| S1 | 공용 React UI·Native Route·Tauri/Windows Toolchain·Installer 전제·회귀 영향 분석 | 영향 Matrix |
| S2 | 공용 UI 직접 재사용·원격 URL 0·최소 Capability·CSP·R1-M3-03 격리 Test 선작성 | 유효 RED |
| S3 | Desktop React Entry·Native Navigation·Production Asset Build 최소 구현 | 전용 JS Test Green |
| S4 | Tauri Rust Shell·Window·Capability·NSIS Bundle 최소 구현 | Rust/Config Test Green |
| S5 | 전체 회귀·Lint·Toolchain·독립성·Frontend Build·Tauri Production Build·공통 Gate | 전부 PASS |
| S6 | 실제 NSIS 설치·App 실행·여섯 화면 클릭·네 Window 크기·접근성·Console 검증 | App JSON·PNG |
| S7 | 정상 종료·Process/Window/Listener/자식 Process 0·동일 설치 App 재기동·재클릭 | Lifecycle JSON |
| S8 | 개발 검증 App 제거·잔존 Process/Port 0·Contract·Evidence Manifest·결과보고·Diff 최종화 | 정식 결과 상태 |

각 단계에서 착수, 세부 단계 완료, 오류·원인·복구, 테스트 완료, 종료 직전에 Progress를 갱신한다. 필수 필드는 시각·단계·상태·변경 파일·명령/테스트 결과·오류/원인/복구·다음 작업이다.

Windows Build·Installer·App 종료는 충분히 기다린다. 파일 잠금이 있으면 관련 `cargo`, `rustc`, `tauri`, App, Installer Process 생존과 대상 경로를 먼저 확인한다. 같은 종료·정리 명령을 근거 없이 반복하지 않고, 다른 쉬운 실행 방식으로 완료 증거를 대체하지 않는다.

## 6. 필수 검증

자동 검증:

- Desktop Entry가 `packages/ui`·Token·Contract를 직접 사용하고 Next/Web Server Module을 사용하지 않음
- Windows 허용 Route와 `native_route_key` 안정 Key, `client_type=windows` 권한 비승격
- Production 정적 자산과 Tauri Config에 앱 정의 원격 URL·Dev Server·내부 주소 0건
- Tauri 예약 내부 Origin Allowlist는 정확값·Artifact·공급망 출처와 앱 설정/호출 0건을 함께 검증하고, Provenance 없는 Binary 문자열은 PASS로 면제하지 않음
- Capability·Permission·CSP 최소 권한과 불필요 Plugin 0건
- Local Service·IPC·Loopback·임시 HTTP Server 0건
- 전용 Test의 RED→GREEN, Rust Test/Check, 전체 순차 회귀, Workspace Lint, Toolchain, Independence, Frontend Production Build, Tauri Production Build, 공통 7범주 Quality Gate
- `git diff --check`, 관련 없는 변경·추적 삭제 0, 승인 정본 Diff 0, Lockfile 변경은 실제 정확 Pin에 한정

Windows App:

- 생성 Installer 파일명·SHA-256·Byte·Bundle Type·서명 상태 `unsigned_development` 기록
- 실제 NSIS 설치 완료, 설치 위치·설치된 실행 파일·Product Version 확인
- 설치된 실제 App에서 Home·Workspace·Account·Organization·Operations·Notifications 클릭
- 1920×1080, 1200×900, 800×900, 500×900 Window에서 상태 보존·가로 Overflow 0
- Keyboard/Focus/Tooltip/Escape/ARIA와 오류·unavailable 표시
- App Process가 원격 Content·Dev Server·Local Service를 시작하지 않고 외부 Interface Listener 0
- 정상 종료 후 App·자식 Process·Window·App 소유 Port 0, 같은 설치 App 재기동 후 핵심 화면 재확인
- 개발 검증 설치본 제거 후 App 관련 Process·Port 0. 사용자 기존 App·설정·자료는 변경하지 않음

Evidence:

- `docs/03_evidence/release_1/R1-M3-02/desktop-shell-build.json`
- `installer-validation.json`, `app-navigation-validation.json`, `app-process-lifecycle.json`
- 네 Window 크기 핵심 PNG와 `unavailable` 상태 PNG
- `evidence-manifest.json`: 저장소 Artifact SHA-256·Byte, Installer/EXE 외부 Build Artifact SHA-256·Byte Metadata, 대상 Commit/환경, 실제/Mock/Deferred 경계
- 실제 외부 효과 0건, DB Migration N/A, Local Service·IPC·Loopback N/A, 서명·Update·Rollback Deferred 명시

생성된 Installer·설치 EXE 자체는 Git에 넣지 않는다. 결과보고에는 재현 가능한 Build 명령, 생성 위치, Hash·Byte와 설치/제거 결과만 남긴다.

## 7. 어울1 검토·서버 통합 경계

- 어울2는 Commit·Push·PR·Merge·ysna-server 배포를 수행하지 않는다.
- 어울1은 Windows 실제 Build·Installer·App 증거를 로컬에서 독립 재검증한 뒤 Commit·Push한다.
- ysna-server는 Linux ARM64이므로 Windows Installer를 대체 검증하지 않는다. Push된 정확 SHA에서 저장소 정합·Node Test·Lint·Toolchain·Independence·공통 Quality Gate만 실행하고 Windows 증거 Metadata와 Manifest를 검증한다.
- DB Migration은 `N/A`다. 서버에서 Shared DB·`common`·`netdata`·`proxy`를 사용하거나 변경하지 않는다.
- Windows 실제 App 검증과 서버 exact-SHA Gate가 모두 통과해야 PR Merge 후보가 된다.

## 8. 결과보고·상태 판정

결과보고 첫 줄:

```text
COMPLETED | R1-M3-02-I001 | 수행 요약 | 변경 파일 | 테스트 근거 | 미해결 위험 | 어울1 검토 요청
```

- `COMPLETED`: 위 산출물과 자동·Installer·App·Lifecycle 증거가 모두 있다.
- `FAILURE_REPORT`: 동일 issue_id, 실패 단계·원인·오류·관련 코드·현재 변경·남은 작업·필요 판단을 포함한다.
- `INCOMPLETE`: 예기치 않은 중단 또는 결과보고 미완성이다. 현재 상태부터 이어갈 수 있게 Progress를 남긴다.
- `BLOCKED`: 권한·환경·승인 경계로 진행할 수 없으며 필요한 결정만 구체적으로 적는다.

중대 미진은 별도 수정 작업지시 대상으로 보고한다. 합격 가능한 경미 보완은 다음 작업에 흡수할 수 있게 구분한다. 사소한 이유로 합격 작업 전체를 다시 열지 않는다.

완료 후 어울2는 추가 쓰기를 중지하고 어울1에게 제출한다.

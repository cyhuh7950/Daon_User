# Daon Evidence Hub와 사용자 제품 분리 설계

## 1. 문서 정보

- 상태: `APPROVED`
- 작성일: 2026-08-11
- 승인 방향: 신산님이 개발·검증용 Evidence Hub는 로그인 없이 관리하고 실제 사용자 프로그램과 분리하도록 승인했다.
- 최종 승인: 2026-08-11 신산님이 본 설계 기준 진행을 승인했다.
- 기준선: 공식 Desktop 정본 `master`, 설계 착수 HEAD `ec0508633803edb975d24321eef19e3407d59eea`.
- 선행 정본: `2026-07-20-daon-user-program-design.md`, `production_bound_prototype_handoff_contract.md`, Windows Recovery 설계 1.3.

## 2. 판정

현재 Web `/`와 Windows `Home`은 최종 사용자 화면이 아니라 M2 `ProductionBoundEvidenceHub`다. Windows `desktop-shell.jsx`의 전역 Header에 Native 로그인 UI가 결합되어 개발용 Evidence 화면에도 사용자 인증이 노출된다. `Workspace`, `Account`, `Organization`, 일부 `Operations` 화면도 `prototype_fixture`·`deferred_actual`을 포함하므로 설치형 Build 성공만으로 사용자 제품이 완성됐다고 볼 수 없다.

이 설계는 Evidence Hub의 검증 자산을 보존하면서 운영 Web·Windows 사용자 제품에서 완전히 제거하고, 실제 사용자 제품을 NotebookLM 참고 3면 Workspace와 실제 Adapter 중심으로 전환한다.

## 3. 현재 근본원인

1. Web `apps/web/app/page.jsx`가 `/`에 `ProductionBoundEvidenceHub`를 직접 렌더링한다.
2. Windows `desktop-shell.jsx`가 `Home`에 같은 Evidence Hub를 렌더링한다.
3. Desktop은 단일 `main.jsx`·단일 Vite Entry·단일 NSIS Build만 사용하므로 Evidence 화면이 제품 Bundle에 포함된다.
4. `NativeAuthPanel`이 Route가 아닌 전역 Titlebar에 있어 Evidence·Prototype·실제 화면의 인증 경계가 섞인다.
5. `platform-prototype-evidence.test.mjs`와 `workspace.test.mjs`가 Home=Evidence Hub를 정답으로 고정한다.
6. M2 Handoff 계약은 Fixture를 실제 Adapter로 교체할 수 있다고 했지만 후속 구현이 Evidence 전용 화면을 제품 진입점에서 제거하지 않았다.

## 4. 검토한 대안

### A. 별도 개발 앱으로 완전 분리 — 채택

- Evidence Hub를 `apps/evidence-hub` 로컬 개발 앱으로 이동한다.
- 사용자 Web·Desktop은 Evidence 모듈을 Import하지 않는다.
- Evidence 앱은 로그인 없이 정적 Fixture·Manifest·검증 상태만 표시한다.
- Production Docker·NSIS Build 대상에서 제외한다.

장점은 인증·배포·사용자 UX 경계가 명확하고 Build 검증이 가능하다는 점이다. 기존 Evidence 자산도 보존한다.

### B. 같은 앱에서 Build Mode로 전환 — 미채택

환경변수나 조건부 렌더링으로 Evidence 화면을 숨길 수 있지만 설정 누락 시 운영 Bundle에 다시 노출될 위험이 있다. 제품과 검증 앱의 의존성도 계속 섞인다.

### C. 관리자 Role 메뉴로 유지 — 미채택

Evidence Hub는 운영 관리자 기능이 아니라 개발 검증 도구다. Role 기반 사용자 메뉴에 남기면 로그인·권한·운영 데이터 계약이 불필요하게 결합된다.

## 5. 목표 구조

### 5.1 개발·검증 Evidence 앱

- 위치: `apps/evidence-hub`.
- 실행: 명시적인 로컬 개발 명령만 제공하며 `127.0.0.1`에만 Bind한다.
- 인증: 없음.
- 데이터: 저장소에 포함된 Fixture·Evidence Manifest·검증 Matrix만 읽는다.
- 외부 효과: API 호출, DB 연결, File Upload, Backup·Restore·Repair, Session 발급 0건.
- 표시: 화면 상단에 `개발·검증 전용 · 사용자 제품 아님`을 고정 표시한다.
- 배포: Production Docker Image, Windows NSIS, Android/iOS Bundle에 포함하지 않는다.
- 향후 실제 운영 API를 호출해야 하는 요구가 생기면 별도 운영 도구 설계와 인증 승인을 먼저 받는다.

기존 `production-bound-evidence-pane.jsx`, `production-bound-evidence-model.js`와 Evidence 전용 CSS·테스트는 이 앱 소유로 이동한다. 사용자 공용 `packages/ui`는 Evidence Hub를 Export하지 않는다.

### 5.2 실제 사용자 Web 제품

- 비인증 `/`: 가입·로그인 화면 또는 로그인 Route로만 진입한다.
- 인증 성공: 현재 Workspace가 있으면 `/workspaces/{workspace_id}`로 이동하고, 없으면 실제 Workspace 목록을 표시한다.
- Browser API는 same-origin BFF만 호출한다.
- Evidence Hub, Mock Adapter, `prototype_fixture`, `deferred_actual`을 사용자 DOM에 표시하지 않는다.
- 실제 미구현 기능은 Fixture 성공으로 대체하지 않고 비활성 상태와 Safe 오류만 표시한다.

### 5.3 실제 Windows 사용자 제품

- 비인증 상태에서는 전역 Header 입력란이 아니라 독립된 Native 로그인 화면만 표시한다.
- 인증 성공 시 기본 진입점은 `WorkspaceDetail`이다. `Home` Evidence Route와 사용자 Navigation 항목을 제거한다.
- 사용자 Navigation 기본값은 `Workspace`, `Notifications`, `Account`다.
- `Organization`과 `Operations`는 실제 권한 Projection이 있을 때만 표시한다.
- Credential은 기존 Rust Vault 경계를 유지하고 WebView에 반환하지 않는다.
- Windows 제품 Bundle은 Evidence Hub 모듈을 Import하거나 포함하지 않는다.

### 5.4 실제 사용자 Workspace

1920×1080 기준 고정 3면을 기본으로 한다.

1. 왼쪽 `Source·지식·권위`: 실제 Source 목록, 등록, 처리 상태, 선택 범위.
2. 가운데 `대화·실행`: 선택 Source 기반 질문, 실행 상태, 답변, Citation·원문 위치.
3. 오른쪽 `업무 Studio`: 보고서·점검표·데이터 표·지식 구조도·문서 초안과 버전·검토·다운로드.

화면 폭이 줄면 기존 상태 계약을 유지한 채 Pane·Drawer·Tab으로 Projection만 바꾸고 Source 선택, 대화, Run, 편집 위치와 Citation 위치를 초기화하지 않는다.

## 6. 실제 데이터 연결 단계

### 단계 A — 제품·Evidence 경계 분리

- 별도 Evidence 앱 생성 및 기존 Evidence 자산 이동.
- Web `/`와 Windows `Home`의 Evidence 연결 제거.
- Windows 비인증 Login 전용 화면과 인증 후 Workspace 기본 진입.
- Product Build의 Evidence Import·문구·Fixture 0건 검증.

이 단계에서는 기존 실제 기능을 새로 만들지 않는다. 제품 화면이 Prototype 성공을 표시하지 않게 만드는 것이 완료 조건이다.

### 단계 B — 핵심 사용자 수직 흐름

- 로그인 후 실제 Workspace·Source 목록 조회.
- PDF 등록과 처리 상태.
- Source 선택 → 질문 → 답변 → Citation 원문 위치.
- Studio 결과 1종 생성·저장·목록 누적.

Web은 same-origin BFF, Windows는 승인된 전용 Tauri Command를 사용한다. Desktop WebView의 직접 `fetch`, Gateway URL, localhost, Docker Host·Port는 금지한다.

### 단계 C — Studio·계정·운영 확장

- Studio 5종 결과와 검토·승인·다운로드·전달.
- 실제 Account·Organization·Notifications 연결.
- Recovery·Operations는 관리자 권한에서만 표시하고 일반 사용자 Navigation에서 분리한다.

## 7. 인증·권한 경계

- Evidence 앱: 로컬 정적 검증 도구이므로 로그인 없음, 외부 API·상태변경 없음.
- 사용자 Web: 기존 Secure·HttpOnly Cookie 로그인 유지.
- Windows 사용자 앱: 기존 Native Login·Credential Manager 유지.
- 인증 실패 시 사용자 Workspace를 렌더링하지 않는다.
- 인증 성공 후에도 Route별 권한 Projection이 없으면 해당 메뉴·Handler 모두 실행하지 않는다.
- 사용자 제품과 Evidence 앱 사이 Session·Storage·Credential 공유는 0건이다.

## 8. Build·배포 경계

- `npm run build:desktop-installer`는 사용자 제품 Entry만 Build한다.
- Evidence 앱은 NSIS와 운영 Docker Build Graph에 포함하지 않는다.
- 정적 Gate는 제품 Source와 최종 Bundle에서 다음을 거부한다.
  - `ProductionBoundEvidenceHub`
  - `prototype_fixture`
  - `deferred_actual`
  - `Mock Adapter`
  - Evidence 앱 Import
- Evidence 앱은 별도 로컬 명령으로만 실행한다.
- 현재 ysna-server 배포 보정 D01은 이 제품 화면 보정과 실제 사용자 여정 검증이 완료될 때까지 `HOLD_PRODUCT_UI_CORRECTION`이다.

## 9. 오류 처리

- 실제 API가 없거나 실패하면 Fixture를 반환하지 않는다.
- 로그인 실패, Session 만료, 권한 없음, Source 처리 실패, 질문 실패, Studio 실패를 서로 다른 Safe 상태로 표시한다.
- 오류에 Password·Credential·Authorization·내부 URL·Loopback Port를 포함하지 않는다.
- Evidence 앱에서 외부 호출 시도가 발생하면 테스트와 Build가 실패해야 한다.

## 10. 테스트 계약

### 구조 경계

- Web·Desktop Product Source에서 Evidence Hub Import 0건.
- Product Bundle에서 Evidence 전용 문자열·Fixture 0건.
- Evidence 앱에서 로그인 UI·Session Bridge·Recovery Adapter·외부 Network 호출 0건.
- Evidence 앱이 Production Build·NSIS·Docker Manifest에 포함되지 않음.

### 사용자 행동

- Web 비인증 → 로그인, 인증 → 실제 Workspace.
- Windows 비인증 → Native 로그인 전용 화면, 인증 → 실제 Workspace.
- 일반 사용자는 Organization·Operations invoke 0건.
- 실제 Source 선택 → 질문 → Citation → Studio 결과 1종 수직 흐름.
- API 실패 시 Prototype 성공 데이터 0건.

### 실제 화면

- Web Production Chrome과 Windows NSIS에서 1920×1080 화면을 각각 확인한다.
- Windows NSIS 첫 화면에 Evidence Hub·개발 배지·Mock Adapter가 없어야 한다.
- Evidence Hub는 로컬 개발 명령에서만 열리고 로그인 없이 동작해야 한다.
- 자동 테스트와 실제 Browser·NSIS 증거를 분리한다.

## 11. 기존 기능 보호

- Evidence 자산과 M2/TP-1 과거 기록은 삭제하거나 다시 쓰지 않는다.
- 기존 사용자 삭제 31건과 미추적 문서 3건을 보존한다.
- Web same-origin, Native Credential Vault, Local Service, Cloud Recovery, DB·Migration 계약을 임의 변경하지 않는다.
- 화면 분리를 이유로 Account·Operations Prototype을 실제 성공으로 승격하지 않는다.
- 실제 사용자 흐름이 연결되기 전에는 Windows 제품 PASS·M5 Exit·R1-WIN-01 PASS를 주장하지 않는다.

## 12. 완료 조건

1. Evidence Hub가 별도 로컬 개발 앱으로만 실행된다.
2. Evidence Hub에는 로그인 UI가 없고 외부 효과가 0건이다.
3. 운영 Web·Windows 사용자 제품에서 Evidence·Prototype UI가 0건이다.
4. 비인증/인증 진입 흐름이 사용자 제품 목적에 맞게 분리된다.
5. 인증 후 NotebookLM 참고 3면 실제 Workspace가 기본 화면이다.
6. 핵심 수직 흐름 `로그인 → Source → 질문 → Citation → Studio 결과`가 실제 API·DB로 검증된다.
7. Web Production Chrome과 Windows NSIS 실제 화면·Network/Native 호출·Process 증거가 있다.

## 13. 결정 기록

- Evidence Hub는 운영 관리자 화면이 아니라 개발·검증 도구다.
- 로그인 없음은 로컬 정적·무외부효과 경계 때문에 허용한다.
- 사용자 제품과 같은 Build에 숨겨 넣지 않고 별도 앱으로 분리한다.
- 사용자 제품의 기본 화면은 Evidence Home이 아니라 실제 Workspace다.
- 구현은 `경계 분리 → 핵심 수직 흐름 → 나머지 실제 화면` 순서로 진행한다.

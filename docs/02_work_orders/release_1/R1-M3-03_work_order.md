# R1-M3-03 작업지시서 — Windows Local Service Shell

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M3-03` |
| issue_id | `R1-M3-03-I001` |
| 작업 | Tauri가 소유하는 Packaged Local Service, Loopback API·IPC, App Instance 인증 골격 구현 |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| Branch/Worktree | `codex/r1-m3-03` · `C:\tmp\Daon_User-r1-m3-03` |
| 기준 SHA | `975ab4ce9fc9d65ee842a7c93805ace9dc432ffe` |
| 선행 작업 | `R1-M3-02 COMPLETED` · PR #16 Merge |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-03_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-03_attempt-1.md` |
| 후속 Gate | M3 Exit 이후 TP-3 전까지 개별 사용자 Gate 없음. 범위·공개 API·데이터·보안 경계 변경은 즉시 어울1 회부 |

어울2는 착수 전에 아래 정본을 EOF까지 읽고 SHA-256을 대조한다. 요약본으로 대체하지 않는다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| `docs/04_test_reports/release_1_test_plan.md` | `359404A190D248E94F2BE4A69CB285D10422FA426C32D4C5409F868F4CA4768B` |
| `docs/04_test_reports/release_1/approval_G2-UX.md` | `007D5927A9D90291F7A190FF56D46B44B5CE58861758C3C25F27B43F4C605583` |
| `docs/01_architecture/DECISIONS.md` | `6BEC74CB940B8F1DB19A3800AEFEA507D04CD5F777EC22BC4EE7185242E36227` |
| `docs/01_architecture/toolchain_dependency_baseline.md` | `28963DA75A4B7EE3A0CDF06F03785A9346BF62FBBCB8FFE212792960D454408D` |
| `docs/01_architecture/monorepo_ownership_boundaries.md` | `CCBBF74E5C7EDE1B96061B649F08DCFD82F7446023AB0117C08A51716E4E753C` |
| `docs/03_evidence/release_1/R1-M3-02/evidence-manifest.json` | `BA4C4EBB65DDF289599D10A812397847F2FD6A91E4AEA597A3EAADD1C20EE33B` |

## 2. 목적과 사용자 관점 완료 조건

R1-M3-03은 사용자가 Python이나 서버 명령을 실행하지 않아도 Windows App이 필요한 Local Service를 함께 설치하고, App 수명주기 안에서 안전하게 기동·감시·종료하는 최소 운영 골격을 만든다.

사용자는 다음을 확인할 수 있어야 한다.

1. 실제 Windows 설치 App을 실행하면 Tauri가 패키지에 포함된 Local Service를 자식 Process로 시작하고 상태를 `starting → ready` 또는 정직한 오류 상태로 표시한다.
2. 서비스는 외부 Interface가 아닌 `127.0.0.1`의 OS 할당 임시 Port에만 Listen하며 Port·Token을 사용자가 입력하지 않는다.
3. App Instance마다 새 ID와 단기 Token을 사용하고, 누락·위조 Token, 다른 Instance ID와 미허용 Command를 거부한다.
4. Browser/React 코드는 Loopback URL·Port·Token을 직접 알거나 호출하지 않고 Tauri의 명시적 Command만 사용한다.
5. App을 정상 종료하면 Local Service Process와 Listener가 남지 않으며 재기동 시 새 Instance로 다시 정상 연결된다.
6. Local Service를 시작할 수 없을 때 `ready`를 가장하지 않고 안정 오류 코드와 재시도 가능 상태를 표시한다.

## 3. 설계 계약

### 3.1 Packaged Local Service

- `services/local-service`에 Python `3.14.3` 기반의 독립 Package를 구현한다. HTTP Service는 승인 설계의 FastAPI/Python 방향을 따르며 FastAPI·Uvicorn·PyInstaller와 Test/Lint/Type/Security 도구를 호환성 확인 후 정확 버전으로 Pin하고 `uv.lock`을 갱신한다.
- Windows 배포물은 PyInstaller로 생성한 단일 실행 파일이며 Tauri Installer의 `externalBin`으로 포함한다. 사용자는 Python·uv·DB CLI를 직접 실행하지 않는다.
- Windows 전용 Sidecar 설정은 `tauri.windows.conf.json`처럼 Platform 분리 설정을 사용하여 Linux ARM64 품질 Gate가 Windows Binary 부재 때문에 거짓 실패하지 않게 한다.
- 생성 EXE, Installer, `node_modules`, `.venv`, `target`, PyInstaller Cache는 Git에 넣지 않는다. 재현 가능한 Build Script와 Hash·Byte Metadata만 기록한다.
- App과 Service의 제품 Protocol Version을 명시하고 호환되지 않는 버전은 안정 오류 코드로 Fail-close 한다.

### 3.2 Process·IPC 수명주기

- Local Service는 Tauri Rust 계층만 시작한다. React/Browser에 Shell·Process 실행 권한을 주지 않는다.
- Rust는 매 App 실행마다 암호학적으로 안전한 무작위 Token과 `app_instance_id`를 생성한다. Secret은 명령행 인자, 환경변수, 로그, UI, Evidence에 남기지 않고 자식 Process의 표준입력 Bootstrap Envelope로 전달한다.
- Service는 `127.0.0.1`과 Port `0`으로 Bind해 OS가 할당한 실제 Port를 취득하고, 준비 완료 후 표준출력의 단일 구조화 Ready Envelope로 Port·Instance·Protocol Version만 부모에게 전달한다. Token은 Ready Envelope에 반환하지 않는다.
- Bootstrap·Ready Envelope에는 최대 크기, 형식, Protocol Version과 제한 시간 검증을 둔다. 예상하지 못한 표준출력이나 시간 초과는 Fail-close 한다.
- Tauri는 자식 Handle과 상태를 단일 소유한다. App 종료 또는 표준입력 EOF 시 Service가 정상 종료하며, 제한 시간 내 종료되지 않으면 해당 자식만 강제 종료한다. 다른 Process를 이름으로 일괄 종료하지 않는다.
- 재기동 때 이전 Token·Instance·Port를 재사용하거나 디스크에 저장하지 않는다. App 하나에서 중복 Service Process를 만들지 않는다.

### 3.3 Loopback API와 App Instance 인증 골격

- Listener는 IPv4 `127.0.0.1`에만 Bind한다. `0.0.0.0`, `[::]`, LAN IP, 외부 Interface와 임의 Host 설정을 거부한다.
- 최소 Endpoint는 Health/Status와 내부 종료 수명주기에 필요한 범위로 제한한다. 임의 Path·Method·Command·Argument 전달 기능을 만들지 않는다.
- 모든 보호 요청은 App Instance ID와 단기 Bearer Token을 검증한다. Token 비교는 Timing Leak을 줄이는 상수시간 비교를 사용하고 실패 응답과 로그는 Secret을 포함하지 않는다.
- Service와 Rust Bridge 양쪽에 명시적 Capability/Command Allowlist를 둔다. Frontend가 호출할 수 있는 Tauri Command도 `local_service_status`, `local_service_retry`처럼 이번 사용자 여정에 필요한 정확 목록만 등록한다.
- CORS를 범용 허용하지 않고 Browser 직접 호출을 지원하지 않는다. Request Body·Header 크기와 Client Timeout을 제한한다.
- Local Service가 제공하는 것은 연결·상태 골격뿐이다. 파일, DB, 지식, LLM, 검색, Sync, Auth/Tenant Business Logic은 구현하지 않는다.
- M4-06이 소유하는 완전한 단기 Token 정책, Process Attestation, 조직 Capability/권한 모델과 감사 정책은 이번 범위에서 확정하거나 우회 구현하지 않는다. 이번 골격은 M4-06이 강화할 수 있는 최소 경계만 제공한다.

### 3.4 Desktop UI·보안 경계

- Desktop UI는 Tauri `invoke`를 통해 명시적 상태 Command만 호출한다. Browser JavaScript·Bundle·Console에 `localhost`, `127.0.0.1`, 실제 Port·Token·내부 URL을 넣지 않는다.
- 기존 M2/M3 React UI, Navigation, Token, 접근성, 반응형 상태를 보존한다. Local Service Badge/상태 영역만 실제 `starting`, `ready`, `unavailable`, `retrying` 결과와 연결한다.
- Tauri Capability에는 Browser용 Shell·Process·HTTP·Filesystem 범용 Permission을 추가하지 않는다. Sidecar 실행은 Rust 내부 소유로 한정한다.
- CSP의 `connect-src 'none'` 원칙을 유지한다. Service 연결 때문에 WebView의 Loopback Network를 허용하지 않는다.
- Secret·Raw Exception·Stack Trace·내부 경로를 UI·Console·Evidence에 노출하지 않는다.
- `apps/web`와 일반 Browser Build의 동작을 바꾸지 않는다. Tauri Runtime이 아닌 환경은 실제 연결을 가장하지 않고 기존 `deferred_actual`/`unavailable` 계약을 유지한다.

## 4. 허용·금지 변경

허용:

- `services/local-service/`의 Package, 실행 Entry, App/Protocol/보안·수명주기 Test
- `apps/desktop/src-tauri/`의 Sidecar 수명주기, 최소 Tauri Command, Windows 전용 Bundle 설정
- `apps/desktop/src/`의 최소 Native Bridge와 실제 상태 연결
- 정확 Pin을 위한 `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `Cargo.toml`, `Cargo.lock`
- 전용 Build·검증 Script와 Root Quality Gate Capability 활성화
- `docs/01_architecture/windows_local_service_contract.md`
- `docs/03_evidence/release_1/R1-M3-03/`, 지정 Progress·결과보고

금지:

- M2/M3 화면·Navigation·Design Token 전체 재작성 또는 범위 외 Refactor
- `apps/web`, Android, iOS, Cloud Gateway 변경
- DB·Migration·파일 이해·지식·LLM·검색·Sync·Auth/Tenant Business Logic 구현
- 완전한 M4-06 인증·조직 권한·Process Attestation을 임의 확정
- Browser에서 Shell/HTTP/Process 범용 Permission 사용
- Browser/React의 절대 API 주소, Loopback URL, Port·Token 직접 접근
- 외부 Interface Listen, 고정 Port, 재사용 Token, Secret의 argv/env/log/UI/Evidence 저장
- 임의 Command/Path/Argument 전달, 범용 CORS, 운영 코드의 Stub Server·Mock 성공
- 서명 Credential·Certificate·Private Key 생성·조회·저장
- Commit·Push·PR·Merge·ysna-server 배포

실제 코드나 Windows 환경이 이 계약과 충돌하면 증거를 Progress에 남기고 범위를 확대하지 않은 채 어울1에게 회부한다.

## 5. TDD·작업 단계

모든 Production 동작은 실패하는 Test를 먼저 작성하고 기대한 이유로 RED를 확인한 뒤 최소 구현으로 GREEN을 만든다. RED·GREEN 명령과 핵심 결과를 Progress에 기록한다.

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| S0 | 정본 EOF·Hash, 기준 SHA, Branch, 단일 Writer, 깨끗한 시작 Diff 확인 | Progress |
| S1 | 현재 Desktop/Local Service/Quality Gate·의존성·Packaging 영향 분석, exact Pin 호환성 사전점검 | 영향 Matrix |
| S2 | Python Protocol·Loopback Bind·인증·Allowlist·수명주기 Test 선작성 | 유효 RED |
| S3 | Local Service 최소 구현과 Python Test Green | Unit/Contract Green |
| S4 | Rust Sidecar 소유·Bootstrap·Ready·Command Allowlist·종료 Test 선작성 후 최소 구현 | Rust RED→GREEN |
| S5 | React Native Bridge·상태 전이 Test 선작성 후 최소 연결 | Frontend RED→GREEN |
| S6 | PyInstaller Sidecar·Tauri Windows Bundle Build와 Quality Gate Capability 활성화 | Build·Gate PASS |
| S7 | 실제 Packaged Service의 인증 거부/허용·외부 Listen 0·정상 종료/재기동 자동 검증 | Lifecycle JSON |
| S8 | 전체 회귀·독립성·Security·Quality Gate, Contract·Evidence·결과보고·Diff 최종화 | 정식 결과 상태 |

각 단계에서 착수, 세부 단계 완료, 오류·원인·복구, 테스트 완료, 종료 직전에 Progress를 갱신한다. 필수 필드는 시각·단계·상태·변경 파일·명령/테스트 결과·오류/원인/복구·다음 작업이다.

Build·Cargo·PyInstaller·npm 명령은 충분히 기다린다. Windows 파일 잠금이 있으면 관련 Process 생존과 대상 경로를 먼저 확인하고 근거 없이 정리 명령을 반복하지 않는다. 화면 App은 열지 않는다. 어울1의 후속 실제 App 검증 전까지 자동·Headless 증거만 제출한다.

## 6. 필수 검증

자동 검증:

- Python Test: Loopback 외 Bind 거부, Bootstrap Schema/Size/Timeout, Token·Instance 누락/위조 거부, 상수시간 비교 경로, 미허용 Method/Path/Command 거부, Secret Redaction, stdin EOF·정상 종료
- Rust Test: 매 실행 새 Token·Instance, 자식 단일 소유, Ready Envelope 검증, Startup Timeout, 제한 종료 후 해당 자식만 Kill, 명시 Command Allowlist
- Frontend Test: Tauri Command만 사용, 상태 전이와 정직한 오류, Browser 환경 성공 가장 0, Loopback URL·Port·Token 노출 0
- Static Contract: 외부 Bind 문자열 0, 범용 CORS 0, Browser Shell/HTTP Permission 0, CSP `connect-src 'none'`, 임의 Command 전달 0
- Package Contract: PyInstaller 실행 파일이 외부 Python 없이 기동, Tauri Windows Bundle에 Sidecar 포함, 생성 Binary Git 추적 0
- Runtime Contract: 실제 Child 기동, Listener가 `127.0.0.1` 한 개, 미인증 거부, 인증된 Status 성공, App/Parent 종료 후 Child·Port 0, 재기동 시 Token·Instance 변경
- Lint·Type Check·Python Security/Audit·Rust Check/Test·Frontend Test/Build·`npm run verify:independence`·공통 7범주 `npm run verify:quality-gate`
- `git diff --check`, 승인 정본 Diff 0, 관련 없는 변경·추적 삭제 0, Lockfile Diff는 실제 exact Pin에 한정

Evidence:

- `docs/03_evidence/release_1/R1-M3-03/local-service-unit.json`
- `local-service-security.json`, `local-service-package.json`
- `desktop-sidecar-contract.json`, `app-service-lifecycle.json`
- `quality-gate-result.json`
- `evidence-manifest.json`: 대상 Commit/환경, 명령·Exit Code, Artifact Hash·Byte, 실제/자동/Deferred 경계
- DB Migration `N/A`, ysna-server `어울1 후속`, Windows 실제 GUI/Installer 수명주기 `어울1 후속`, M4-06 보안 강화 `Deferred` 명시

생성된 실행 파일·Installer 자체는 Git에 넣지 않는다. 결과보고에는 재현 가능한 Build 명령, 생성 위치, Hash·Byte, Test 결과만 남긴다.

## 7. 어울1 검토·서버 통합 경계

- 어울2는 Commit·Push·PR·Merge·ysna-server 배포를 수행하지 않는다.
- 어울1은 결과 Diff와 자동 증거를 검토하고 Windows 실제 Installer/App에서 상태·인증 거부·외부 Listen 0·정상 종료를 독립 재검증한다. 화면을 사용하면 검증 직후 App과 관련 Process를 종료한다.
- 승인된 Diff만 Commit·Push한 뒤 ysna-server `/home/ubuntu/deploy/daon-user/R1-M3-03` 격리 경로에서 exact SHA를 검증한다.
- ysna-server Linux ARM64 검증은 Source·Python Service·Node/Rust Test·Lint·Independence·Quality Gate 대상이며 Windows Installer의 대체 증거가 아니다.
- DB Migration은 `N/A`다. 서버의 `shared-db`, `common`, `netdata`, `proxy`를 사용하거나 변경하지 않는다.
- Windows 실제 App 검증과 서버 exact-SHA Gate가 모두 통과해야 PR Merge 후보가 된다.

## 8. 결과보고·상태 판정

결과보고 첫 줄:

```text
COMPLETED | R1-M3-03-I001 | 수행 요약 | 변경 파일 | 테스트 근거 | 미해결 위험 | 어울1 검토 요청
```

- `COMPLETED`: 지정 산출물과 자동·Package·Runtime·Lifecycle 증거가 모두 있다.
- `FAILURE_REPORT`: 동일 issue_id, 실패 단계·원인·오류·관련 코드·현재 변경·남은 작업·필요 판단을 포함한다.
- `INCOMPLETE`: 예기치 않은 중단 또는 결과보고 미완성이다. 현재 상태부터 이어갈 수 있게 Progress를 남긴다.
- `BLOCKED`: 권한·환경·승인 경계로 진행할 수 없으며 필요한 결정만 구체적으로 적는다.

중대 미진은 별도 수정 작업지시 대상으로 보고한다. 합격 가능한 경미 보완은 다음 작업에 흡수할 수 있게 구분한다. 사소한 이유로 합격 작업 전체를 다시 열지 않는다.

완료 후 어울2는 추가 쓰기를 중지하고 어울1에게 제출한다.

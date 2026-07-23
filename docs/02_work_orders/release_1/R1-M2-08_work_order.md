# R1-M2-08 작업지시서 — 플랫폼별 Production-bound Prototype Evidence Pack

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M2-08` |
| issue_id | `R1-M2-08-I001` |
| 작업 | M2-01~07 UX·상태·오류·권한 자산을 하나의 클릭 가능한 Evidence Hub와 플랫폼별 M3 승계 계약으로 정합화 |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| Branch/Worktree | `codex/r1-m2-08` · `C:\tmp\Daon_User-r1-m2-08` |
| 기준 SHA | `e55de40d7ab69dbbe2b5c19c6b1596e8c847a199` |
| 선행 작업 | `R1-M2-01`~`R1-M2-07` 완료·Merge |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M2-08_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-08_attempt-1.md` |
| 중요 Gate | 완료·독립 검토·원격 검증 뒤 `TP-1/G2-UX`; 어울1이 신산님께 보고하기 전 M3 진입 금지 |

어울2는 착수 전에 아래 정본을 EOF까지 읽고 SHA-256을 대조한다. 요약본으로 대체하지 않는다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| `docs/04_test_reports/release_1_test_plan.md` | `359404A190D248E94F2BE4A69CB285D10422FA426C32D4C5409F868F4CA4768B` |
| `packages/contracts/navigation.json` | `4FB54727E381CA5D89BD310B2D8315E61153DF46AC1CC0E41070A3FE7D9C6377` |
| `packages/contracts/screens.json` | `0167274173DBD4C7AB6A444279B7F90150858BB998DCC91608CBED54D667F720` |
| `docs/01_architecture/product_sitemap.md` | `102F1D1C5E47398E426EB0028200E8ED74F010428990EABA9B527C2AEA554925` |
| `docs/01_architecture/source_authority_prototype_adapter_contract.md` | `6B0E49855920FA3AE82A3327532CE5E88C1B1CE05680AE440C91B316666F6F0D` |
| `docs/01_architecture/run_model_evidence_prototype_adapter_contract.md` | `21C4F4339D15F06084B10281C40D4BEA1B1B894325C90074EF25007E94CAB6E8` |
| `docs/01_architecture/studio_workflow_prototype_adapter_contract.md` | `7F14503F6FBE8244642C556EFA93955110B4E674A80D705B94A58CFF17E8C5F4` |
| `docs/01_architecture/account_security_prototype_adapter_contract.md` | `667199827C7B4DEE7C44ECBFCE52A92AA191E60FD899271D2E67639C5E65A63D` |
| `docs/01_architecture/operations_recovery_prototype_adapter_contract.md` | `6EAA0CAC7DD1013D3A85A0B91D398A819FF7F42E8AB8D75B2392EE095BF0E1A7` |

M2-02~07의 `docs/03_evidence/release_1/R1-M2-0*/evidence-manifest.json`도 모두 읽고 Artifact 존재·Hash·Byte를 재검증한다. M2-01은 IA·Route·Token 정본과 서버 검증 Manifest를 기준으로 대조한다.

## 2. 목적과 합격 관점

M2 Exit의 목적은 Native 실행 파일이나 실제 Backend를 미리 만드는 것이 아니다. 승인된 적응형 3면 Workspace와 M2-01~07의 화면·상태·오류·권한 계약이 한 제품 흐름으로 연결되고, M3가 폐기하지 않고 승계할 자산과 교체할 Mock Adapter가 명확하다는 것을 실제 Browser와 기계 검증으로 입증하는 것이다.

최소 사용자 여정 8종을 Evidence Hub에서 추적한다.

1. Home에서 Workspace로 진입하고 선택·Pane·Evidence 위치를 보존한다.
2. 사용자 파일/직접 입력, 인터넷, LLM 일반 지식, Daon 승인 지식·RuleSet, 등록 생산 지식의 권위·가중치·충돌 상태를 확인한다.
3. Local LLM을 포함한 선택 Mode·Frozen Run Snapshot·Fallback·비용·근거 계보를 확인한다.
4. Studio Tile 선택→생성 설정→확정→제출→Version 비교를 확인한다.
5. 검토·승인·반려·재승인·Export/Delivery·명시 생산 지식 등록 Gate를 확인한다.
6. 계정·조직·장치·정책·Step-up·현재 권한 재검증을 확인한다.
7. 운영 경고→제한→`waiting_model` 자동/수동 새 Run→복구와 알림 Deep Link를 확인한다.
8. `loading/empty/warning/error/forbidden/unavailable`과 장애별 축소 운영이 정상 화면과 같은 흐름에서 존재하며 성공으로 위장하지 않음을 확인한다.

## 3. 플랫폼별 정직성 계약

| 플랫폼 | M2에서 직접 증명 | 이번에 금지하는 주장 | M3 승계 Owner |
| --- | --- | --- | --- |
| Web | 실제 Next Production Build, 실제 Chrome 클릭, 네 폭 1920/1200/800/500, Route·상태·키보드·Console·Resource Timing | 실제 API/DB/LLM/File/Delivery 성공 | R1-M3-01 |
| Windows | Web 공용 React UI의 `client_type=windows` Projection, 기능/권한 Matrix, Tauri Host가 재사용할 Route·State·Adapter 계약 | Tauri EXE Build·설치·IPC·Local Service 실행 완료 | R1-M3-02·03 |
| Android | Navigation/Screen 정본과 Mobile Allowlist의 `client_type=android` Projection, Mobile에서 가능한 조회·질문·검토와 Web/Windows 이어서 작업 안내 | APK Build·설치·실기기·Native Gateway 호출 완료 | R1-M3-04·05 |
| iOS | Android와 같은 Contract Projection, iOS Build Host/서명 준비상태와 unavailable 사유 | Archive/IPA·Simulator/실기기·서명 완료 | R1-M3-04·06 |

- 화면 폭으로 플랫폼을 추론하지 않는다. `client_type`은 명시 입력이며 역할·권한을 만들지 않는다.
- `operator` 등 NavigationPersona와 MembershipRole은 분리한다. Persona에서 Capability나 Write를 추론하지 않는다.
- Android/iOS는 DOM 기반 `packages/ui`를 Import하지 않는다. M2 Evidence Hub가 보여주는 Mobile 상태는 Contract Projection이며 Native 화면 실행이 아니다.
- Windows는 공용 React UI 재사용 계약을 증명하되 Tauri 실행 완료로 표시하지 않는다.

## 4. 구현 범위

### 4.1 M2 Evidence Hub

- 기존 `/` Home Route를 M2 Evidence Hub 진입점으로 확장한다. `home` Route/Screen ID와 M3 Owner를 바꾸지 않는다.
- 8개 여정, Web·Windows·Android·iOS 검증 수준, 현재 `prototype_fixture | contract_projection | deferred_actual | unavailable`을 직접 표시한다.
- Workspace·Account·Organization·Operations·Notifications의 실제 기존 Route로 이동할 수 있어야 한다. Workspace 내부 Source·Run·Studio Pane 진입 방법도 제공한다.
- Hub는 기존 Domain Model을 복사하지 않고 공개 Export와 Evidence Manifest를 조합한다. 기존 M2 기능 코드를 재작성하지 않는다.
- 상태·플랫폼 선택과 여정 Check 상태는 폭 전환·Route 왕복 뒤 보존돼야 하며, 실제 서버 저장 성공으로 표시하지 않는다.

### 4.2 기계 판독 Evidence Matrix

- 순수 `ProductionBoundEvidenceModel` 또는 동등한 전용 Model에 플랫폼, 8개 여정, Route, 필요한 화면·상태, M2 검증 수준, M3 Owner, Mock Adapter, Evidence 경로를 고정한다.
- 각 Journey는 `verified_prototype`, `contract_projection`, `deferred_actual`, `blocked`를 혼합하지 않고 플랫폼별로 별도 판정한다.
- `deferred_actual`과 `unavailable`은 PASS 수에 포함하지 않는다.
- M2-02~07 Manifest의 Artifact가 없거나 Hash·Byte가 다르면 Evidence Pack을 `COMPLETED`로 만들지 않는다.
- `packages/contracts/navigation.json`·`screens.json`에 없는 Route/Screen/Client/State를 임의 생성하지 않는다.

### 4.3 오류·권한·Mock 정직성

- 정상 여정뿐 아니라 `warning/error/forbidden/unavailable`, 중요 충돌, 비용 차단, Step-up/G9, 권한 회수, Evidence Store 장애를 Hub에서 해당 실제 화면으로 연결한다.
- 실제 Adapter가 없는 Action은 `prototype_fixture` Preview와 `deferred_actual`을 함께 표시한다.
- 실제 Browser Network가 없거나 Resource Timing을 사용할 수 없으면 0건으로 추정하지 않고 `unavailable` 사유와 정적 검사를 분리한다.
- Secret·Credential·개인정보·Raw Provider 오류·내부 Host/Port·Chain-of-Thought를 Evidence·DOM·Console에 넣지 않는다.

### 4.4 M3 승계 계약

`docs/01_architecture/production_bound_prototype_handoff_contract.md`를 생성해 다음을 표로 고정한다.

- 재사용: Route/Screen/Token, Workspace State/Interaction, Source/Run/Studio/Account/Operations 순수 Model·Reducer, 안전 Code, 접근성·반응형 CSS
- 교체: Fixture Data, Browser-local Preview, Mock Adapter, Evidence Hub 전용 상태
- 플랫폼 Owner: R1-M3-01~06
- 실제 Adapter Owner: M4~M9
- Web same-origin BFF, Windows 승인 IPC/Loopback, Mobile HTTPS Public Gateway 경계
- M2가 실제 완료로 주장하지 않는 항목과 M3/후속 검증 ID

## 5. 허용·금지 변경

허용:

- `apps/web/app/page.jsx`의 Home Evidence Hub 연결
- `packages/ui/src/`의 M2 Evidence 전용 Model·Pane·최소 Export·CSS
- `scripts/tests/platform-prototype-evidence.test.mjs`
- `docs/01_architecture/production_bound_prototype_handoff_contract.md`
- `docs/03_evidence/release_1/R1-M2-08/`
- 지정 Progress·결과보고

금지:

- `packages/contracts/navigation.json`, `screens.json`, Design Token 정본 수정
- Desktop/Mobile 실제 Shell·Tauri·React Native 화면·Native Gateway·IPC·Local Service 구현
- 실제 API·Auth·DB·Migration·Queue·LLM·File·Export·Delivery·복구 실행
- Dependency·Lockfile·Toolchain·CI 변경, R1-D022 완화
- 기존 M2-01~07 Model·Pane·Fixture 재작성 또는 무관 Refactor
- Browser 코드의 절대 API 주소, localhost, Docker Host/Port, `NEXT_PUBLIC_API_BASE_URL`

## 6. TDD·작업 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| S0 | 정본 Hash·기준 SHA·Branch·단일 Writer·기존 Diff 0 확인 | Progress |
| S1 | 8개 여정·4플랫폼·Mock 정직성·Manifest 공격 Test 선작성 | 유효 RED |
| S2 | 순수 Evidence Model·Manifest 검증기 최소 구현 | 전용 Test Green |
| S3 | Home Evidence Hub와 실제 기존 Route 연결 | Navigation·상태 보존 Green |
| S4 | 오류·권한·축소 운영·M3 승계 Matrix 연결 | 부정 상태 직접 표시 Green |
| S5 | 네 폭 Production Browser 실제 클릭·Keyboard·Console·Resource Timing | Browser JSON·PNG |
| S6 | Windows/Android/iOS Contract Projection과 Native 미실행 정직성 | Platform Matrix·Test |
| S7 | M2-01~07 Artifact Hash·Byte 전수, 전체 회귀·Lint·Build·Gate | 전부 PASS |
| S8 | Handoff 계약·Evidence Manifest·보고·Diff 최종화 | `COMPLETED` 또는 정식 보고 |
| S9 | 어울1 검토·Commit·Push·GitHub·ysna-server exact SHA | Required Check·ARM64 PASS |
| S10 | 최신 Diff 독립 검토 | ACCEPT 또는 REWORK |
| S11 | 어울1 Merge 후 TP-1 기술 의견서 작성 | 신산님 G2-UX 보고 대기 |

각 단계에서 착수·완료·오류·복구·테스트·다음 작업을 Progress에 즉시 기록한다. Windows 파일 잠금은 Process 생존을 확인하며 길게 기다리고 중복 Patch·덮어쓰기·검증 우회를 하지 않는다.

## 7. 필수 자동·Browser 검증

자동 검증:

- 8개 Journey ID·4개 Client·M3 Owner·Route/Screen·상태·Evidence 누락 0
- Navigation/Screen 정본 밖 값, Artifact 누락/변조, `deferred_actual`의 PASS 위장 Fail-close
- `operator` Persona 권한 추론 0, Client/폭에 따른 Capability 생성 0
- Mobile DOM UI Import 0, Windows 실제 IPC/Local Service 성공 주장 0
- 기존 M2-01~07 전체 회귀, Workspace Lint, Production Build, 공통 Gate

Production Browser:

- `/` Evidence Hub에서 8개 여정과 4플랫폼 검증 수준 직접 표시
- 실제 Workspace·Account·Organization·Operations·Notifications Route 이동과 복귀
- `loading/empty/warning/error/forbidden/unavailable`, 중요 충돌, 비용·Step-up·G9·Evidence 장애 연결
- 1920×1080, 1200×900, 800×900, 500×900 상태 보존·가로 Overflow 0
- Keyboard/Focus/Tooltip/Escape/ARIA, Console warning/error 0
- Network/Resource Timing 가용 여부, 비동일 Origin·API-like 요청·금지 내부 주소를 분리 기록

Evidence:

- `docs/03_evidence/release_1/R1-M2-08/evidence-manifest.json`
- `platform-journey-matrix.json`, `m3-handoff-matrix.json`, `browser-validation.json`
- 네 폭 Hub PNG와 오류·권한·unavailable 직접 상태 PNG
- Artifact SHA-256·Byte, 대상 Commit/환경, Fixture/Projection/Actual 경계

## 8. 결과보고·완료 조건

결과보고는 `판정 → 판단 이유 → 조치` 순서와 아래 첫 줄을 사용한다.

```text
COMPLETED | R1-M2-08-I001 | 수행 요약 | 변경 파일 | 테스트 근거 | 미해결 위험 | 다음 판단
```

다음이 모두 충족되어야 `COMPLETED`다.

- 8개 여정이 하나의 실제 Web Prototype 흐름과 Evidence Matrix로 연결됨
- 4플랫폼의 M2 증명·미실행·M3 Owner가 정직하게 분리됨
- M2-01~07 Manifest/Artifact 전수 Hash·Byte 일치
- 정상뿐 아니라 오류·권한·축소·unavailable 상태가 실제로 존재함
- 전용·전체 회귀·Lint·Build·Gate·Browser가 모두 PASS
- Handoff 계약·Evidence·Progress·결과보고 완비
- 관련 없는 변경, Dependency/Lockfile/Toolchain/Contract 정본 변경, 실제 외부 효과 0건

어울2는 Commit·Push·PR·Merge·ysna-server·TP-1 판정을 수행하지 않는다. 완료 후 쓰기를 중지하고 어울1에게 제출한다.

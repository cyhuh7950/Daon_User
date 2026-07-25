COMPLETED | R1-M3-02-PREDECESSOR-LOCK-SUCCESSOR | R1-M2-06·07 역사 Manifest를 보존한 PostCSS Lockfile 후속 대체 계보와 필드별 Fail-close 계약 구현 | predecessor reconciliation 코드·행동 Test, R1-M2-08 Result/Summary, R1-M3-02 Generator·Source/Evidence Manifest, FIX-05 문서·Progress·Attempt-2 정합화 | 전용 20/20, 전체 214/214, Lint, Web/Desktop Build, Desktop Type, 7범주 Gate, Audit High/Critical 0, Reconciliation 90·80/6/4/0, JSON 11/11, Hash·Byte 85/85, Generator 2회 결정성 PASS | 없음 | 어울1의 최종 Diff·증거 기술 검토

# R1-M3-02 FIX-05 최종화 결과보고 — Attempt 2

## FIX-05 최종 판정

`COMPLETED`

- 정식 `FAILURE_REPORT`가 아니며 실패 횟수는 `0회`다.
- R1-M2-06·07 역사적 `evidence-manifest.json`의 이전 `package-lock.json` 기대값은 변경하지 않았다.
- 두 Lockfile Artifact만 승인된 R1-M3-02 PostCSS `8.5.23` 후속 대체로 분류하고 Reconciliation을 `90 / DIRECT_MATCH 80 / SUCCESSOR_SUPERSEDED 6 / LEGACY_MANIFEST_DRIFT 4 / UNEXPLAINED_MISMATCH 0`으로 갱신했다.
- Work Order·경로·이전 SHA/Byte·origin Commit·successor Commit `8fafe2fd...`·현재 SHA/Byte 중 하나라도 다르거나 현재 checkout이 Git canonical/LF·CRLF 표현과 다르면 `UNEXPLAINED_MISMATCH`로 Fail-close한다.
- FIX-05 RED `17/20`에서 최소 구현 뒤 GREEN `20/20`, 전체 순차 `214/214`로 전환했다.
- 제품 기능·화면·공개 API·데이터·보안·PostCSS/Next/Vite/Lockfile 내용은 변경하지 않았다.
- 경미 보완으로 테스트 표시명의 과거 `90·82/4/4/0` 문구만 최신 승인 집계 `90·80/6/4/0`으로 정합화했으며 실행 계약은 변경하지 않았다.

## FIX-04 최종 판정

`COMPLETED`

- 정식 `FAILURE_REPORT`가 아니며 실패 횟수는 `0회`다.
- EACCES/EIO Probe 주입 행동은 Exit `2`, Cargo Child `0`, Temp Target `0`, 정확 `gen`·인접 경로 무변경으로 GREEN이다.
- 갱신 승인된 Root Override `{ "postcss": "8.5.23" }` 단일 항목과 Lockfile을 적용했고 Next `16.3.0-canary.93`·Vite `8.1.5` 자체 버전 및 PostCSS 항목을 제외한 Package Graph SHA-256 `49a32ff6e416651358ef5638da18aa2be4de4e04d7f47268cc2ad5f5d1cfd0ca`는 유지됐다.
- Lockfile과 `npm ci --offline` 뒤 실제 설치 Tree에서 Next·Vite는 모두 PostCSS `8.5.23`을 사용한다.
- `npm audit --omit=dev --audit-level=high --json`은 Exit `0`, High `0`, Critical `0`이며 `GHSA-6g55-p6wh-862q`와 `GHSA-r28c-9q8g-f849`는 해소됐다.
- npm `11.12.1`의 `npm ls ... --json` Exit `1`은 Root `problems`의 단일 `postcss@8.5.23 invalid`와 재귀 invalid 사유 `"8.5.10" from node_modules/next`만 존재한다. missing·extraneous·다른 invalid는 `0`으로 갱신 작업지시의 동등 합격 계약을 정확히 충족한다.
- Web·Desktop Build, 전체 회귀, Desktop Type, 공통 Quality Gate와 Evidence Generator까지 모두 재실행해 이전 `BLOCKED` 상태를 해소했다.

## 판정

`COMPLETED`

- 정식 `FAILURE_REPORT`가 아니며 동일 Issue의 실패 횟수는 `0회`다.
- FIX-03이 요구한 기존 `gen` 보존 Fail-close, 이번 실행 생성 `gen` 한정 정리, 행동 테스트와 Evidence 정합화를 완료했다.
- FIX-02의 자체 재현 Cargo 경계, 실제 설치 Release App L4 관찰, Windows Build 입력 최소화 증거는 그대로 유지했다.
- 이전 Computer Use Helper Timeout은 제품 실패가 아니며, 어울1이 동일 설치본에서 독립 L4 관찰을 완료해 `BLOCKED` 원인이 해소된 상태를 유지한다.

## 판단 이유

### FIX-04 Probe·PostCSS 보안 계약

- Probe 상태 확인이 EACCES 또는 EIO로 실패하면 Child와 Temp Target을 만들기 전에 안정 Exit `2`로 Fail-close하며 기존 경로와 인접·다른 Worktree를 변경하지 않는다.
- Root Override는 `postcss=8.5.23` 단일 항목이고 Lockfile·실제 설치 Tree의 단일 PostCSS Package를 Next와 Vite가 함께 해석한다.
- Next·Vite 자체 버전과 비PostCSS Graph는 고정됐고 `npm audit`의 High/Critical과 전체 취약점은 모두 `0`이다.
- `npm ls` Exit `1`은 승인된 Next exact `8.5.10` 대비 Override 단일 invalid에 한정됐으며 missing·extraneous·다른 invalid가 없다. Gate 완화나 Advisory 예외는 적용하지 않았다.

### Cargo·Gate 자체 재현성

- 교차 플랫폼 Node Wrapper는 `os.tmpdir()` 아래의 충돌 없는 전용 Cargo Target을 사용한다.
- `verify:desktop-type`과 `build:desktop-installer`는 호출자의 수동 `CARGO_TARGET_DIR` 없이 실행된다.
- FIX-03 행동 테스트는 구현 전 예상 RED `7/8`에서 구현 후 GREEN `8/8`로 전환됐다.
- Wrapper는 Cargo Child와 Temp Target을 만들기 전에 정확한 Desktop Tauri `gen`을 확인한다. 기존 `gen`이 존재하거나 상태 확인이 불가능하면 Child 호출 `0`, Target 생성 `0`, 안정 Exit `2`로 Fail-close한다.
- 실행 전 `gen` 부재가 확인된 경우에만 Cargo 실행 뒤 이번 실행이 만든 정확한 `gen`을 정리한다.
- Exit `0`, Exit `23` 전파, Spawn Error의 안정 Exit `2`, 기존 `gen` Sentinel Byte/Hash, 같은 Worktree 인접 Sentinel, 다른 Worktree Sentinel, Temp Cargo Target 외 경로 보존 행동 테스트가 모두 통과했다.
- Windows Node 24에서 `npm.cmd` 직접 Spawn이 `EINVAL`을 반환하는 원인을 재현한 뒤, Shell 문자열 없이 현재 Node와 `npm_execpath`를 배열 인자로 실행하도록 수정했다.
- 저장소 내부 `apps/desktop/src-tauri/target`과 Root `target`을 만들지 않는다.

### Installer·실제 App

- Installer: `C:\Users\cyhuh\AppData\Local\Temp\daon-user-desktop-installer-J8IhJv\release\bundle\nsis\Daon 사용자 프로그램_0.1.0_x64-setup.exe`
  - `1,366,987 bytes`
  - SHA-256 `F92AAD047033AC6AD6C06464E5C596F605D5ED94E075EEED807D010669DD2918`
  - NSIS x64, `unsigned_development`
- 설치 EXE: `C:\Users\cyhuh\AppData\Local\Daon 사용자 프로그램\daon-user-desktop.exe`
  - `4,066,304 bytes`
  - SHA-256 `0D77B7B1CA1A0724D8D364776384B98FE6AA1305123431CAAEEC141328165348`
  - Product Version `0.1.0`
- 위 Installer와 설치 EXE의 Path·Byte·SHA-256은 과거 검증 Artifact의 재현 Metadata이며 현재 파일 존재를 주장하지 않는다.
- 어울1 독립 검토 뒤 NSIS `uninstall.exe /S`는 Exit `0`이었다.
- Cleanup 뒤 `C:\Users\cyhuh\AppData\Local\Daon 사용자 프로그램`, `HKCU\Uninstall\Daon 사용자 프로그램`, `C:\Users\cyhuh\AppData\Local\Temp\daon-user-desktop-installer-J8IhJv`는 모두 `exists_after_cleanup=false`이고 Daon App Process는 `0`이다.
- 사용자 기존 자료와 다른 경로 변경은 `0`이다.

### 정확 GUI·접근성·상태

- 정확 Win32 ClientRect `1920×1080`, `1200×900`, `800×900`, `500×900`과 Frame 포함 캡처 `1922×1112`, `1202×932`, `802×932`, `502×932`를 확인했다.
- 네 크기에서 상태 보존과 가로 Overflow `0`을 관찰했다.
- `500×900`에서 Home·Workspace·Notifications·Account·Organization·Operations를 실제 클릭하고 각 Accessibility 문서 영역 전환을 확인했다.
- Operations는 재확인하여 `운영·알림·복구 / 운영 상태·복구`를 확인했다.
- Keyboard Tab 뒤 운영 상태 Button의 이중 파란 Focus Ring을 확인했다.
- ARIA/UIA Button `API·Worker·DB·Object Storage 안전 상태 설명`, Tooltip 설명, Escape 뒤 Tooltip Node 제거와 Trigger Button 유지를 확인했다.
- `Web · unavailable`, `Web · error`를 실제 선택했고 성공 또는 healthy로 표시되지 않았다.
- 같은 설치 App 재기동 뒤 Home Evidence Hub와 Workspace의 `Release 1 운영 준비`, `실행 unavailable`, `two-pane`, `프로토타입 데이터`를 재확인했다.
- 핵심 네 크기·Focus·Tooltip·error·unavailable 실제 PNG 8개를 Evidence Pack에 저장했다.

### Runtime·보안 경계

- 실행 Root PID `97560`, 자식은 `conhost.exe` 1개와 Microsoft WebView2 Runtime 6개였다.
- 앱 정의 Local Service·Backend·Dev Server Process, TCP Endpoint·Listener·Remote Connection, UDP Endpoint는 모두 `0`이었다.
- `frontendDist=../dist`, `devUrl`·`beforeDevCommand` 없음, CSP `connect-src 'none'`을 Runtime 관찰과 분리해 기록했다.
- Release Console/DevTools는 직접 관찰하지 못했으므로 `not_observable_in_release_build`로 남기고 PASS로 추론하지 않았다.
- 최종 종료 뒤 App·Window·관련 WebView2 Process·TCP·UDP는 `0`이다.

### Evidence·최소화

- Windows Bundle 입력은 `apps/desktop/src-tauri/icons/icon.ico` 1개만 유지했다.
- Tauri가 Build 중 재생성한 `gen/schemas` 4개와 비Windows Icon은 보존하지 않았다.
- `source-artifact-manifest.json`에 실제 Build 입력 전체와 검증 입력 `13개`를 구분해 Hash·Byte로 기록했다.
- `build.rs`, `src/main.rs`, `app-icon.svg`, `icon.ico`, Tauri Config·Capability, Cargo Lock/Manifest, Desktop Entry/CSS, 공용 UI·Token·Contract, Wrapper·Test를 포함했다.
- Installer·설치 EXE는 어울1 독립 검토 완료 뒤 제거했으며, 과거 Path·Hash·Byte Metadata만 `historical_reproducibility_record`로 보존했다.

### 최종 자동 검증

- FIX-04 PostCSS 계약 Test: RED `0/1` → 전용 전체 `10/10 PASS`
- FIX-05 predecessor reconciliation Test: RED `17/20` → 전용 전체 `20/20 PASS`
- 전체 순차 Test: `214/214 PASS`
- Workspace Lint: `11 files PASS`
- Web Production Build: Next `16.3.0-canary.93`, Compile·TypeScript·7개 Static Page PASS, Exit `0`
- Desktop Production Build: Vite `8.1.5`, `42 modules`, Exit `0`
- 수동 `CARGO_TARGET_DIR` 없는 Desktop Type: Rust `1.97.1` locked Check, Exit `0`
- 수동 `CARGO_TARGET_DIR` 없는 공통 Quality Gate: 7범주 전부 PASS, failures `0`, Exit `0`
- Production Audit: Exit `0`, High `0`, Critical `0`, 전체 취약점 `0`
- `npm ls next vite postcss --all --json`: Exit `1`, 허용된 Next exact `8.5.10` 대비 PostCSS `8.5.23` 단일 invalid만 존재, missing·extraneous·다른 invalid `0`
- 최종 Gate가 재생성한 R1-M1-05 Evidence 2개는 R1-M3-02에 보존 후 Git 기준선으로 원복했다.
- 최종 검증 입력은 `13개`, R1-M3-02 JSON Parse는 `11/11 PASS`, Source/Evidence Hash·Byte 재검산은 `85/85 PASS`다.
- Source Manifest는 `12931 bytes`, SHA-256 `33778A715A4B8FBBCFC5F570F5DAAEB20746DB7352BCAB3359E5C5DA385B3986`이고, Evidence Manifest는 `7115 bytes`, SHA-256 `D1512A255A8D9AF864C39F9A757ED71431DF328BF021874B30F226F42DED7A03`이다.
- 승인된 정확 Worktree에서 Generator를 두 번 재실행했고 두 실행 모두 Source `52`, Validation `13`, Evidence `19`와 위 Manifest SHA-256이 동일해 결정성 PASS다.
- R1-M2-08 Reconciliation JSON은 `94950 bytes`, SHA-256 `778EB5B1CD397285320E3F37E2AB35CB6457EC7F7BC2077CBC8BE1E49E4FBC3A`이고, 갱신된 Library·Test·Reconciliation 3개 Artifact Hash·Byte 불일치가 `0`이다.
- `git diff --check` 오류 `0`, R1-M1-05 Evidence Dirty `0`, `gen`·Desktop/Root Cargo Target·Temp Check Target·Daon App Process 잔존 `0`을 확인했다.

## 조치

- 제품 변경은 Desktop Shell과 자체 격리 검증 경계에 한정했고 Web·API·데이터·Local Service·공개 계약을 확장하지 않았다.
- 관련 없는 R1-M1 생성 Evidence는 R1-M3-02 최신 Gate 결과를 보존한 뒤 Git 기준선으로 원복했다.
- Release Console은 `not_observable_in_release_build`로 유지하며 PASS로 추론하지 않는다.
- Installer Temp Target과 설치본은 어울1 독립 검토 뒤 제거 완료됐고 설치 경로·Uninstall Registry·Daon App Process 잔존 `0`을 Evidence에 반영했다.
- 화면·설치 App 재실행, Commit·Push·PR·배포는 수행하지 않았고 각각 `0건`이다.
- 최종 지정 검증 결과와 Manifest Hash/Byte를 반영한 Attempt-2를 어울1에게 제출한다.

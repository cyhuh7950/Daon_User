COMPLETED | R1-M3-02-GUI-500-OVERFLOW | Windows Tauri Shell과 500px 주 탐색 Overflow 수정 완료 | Desktop/Tauri Source·계약·R1-M3-02 Evidence·Test·Gate 등록 | RED 5/6→GREEN 6/6, 전체 209/209, Lint 11, Build·NSIS·Quality Gate·설치 앱 6경로/4크기 PASS | 서명·Update·Rollback과 Local Service는 승인 계획대로 후속 범위 | 어울1 최종 Diff·Evidence 검토 및 Commit/Push 판단

# R1-M3-02 결과보고

## 판정

`COMPLETED`

## 판단 이유

- 승인 정본 10개를 EOF까지 확인했고 SHA-256 `10/10`이 작업지시서와 일치했다.
- G2-UX `APR-G2-UX-20260723-01` `GO`와 단일 Writer·지정 Branch/Worktree를 확인했다.
- 공용 React UI·Token·Navigation/Screen Contract를 직접 소비하는 Tauri 2 Windows NSIS Shell을 구현했다.
- 실제 설치 앱 500×900에서 재현된 주 탐색 수평 Scrollbar 결함을 실패 테스트로 먼저 고정하고 Desktop CSS 3개 선언만 수정했다.
- 재Build·재설치한 실제 App에서 6개 경로, 4개 Window 상태, 정상 종료를 검증했다.
- 전체 회귀·Lint·Build·공통 Gate를 통과했고 설치본·Process·외부 Cargo Target을 정리했다.

## 조치

### 변경 내용

- `apps/desktop`
  - 공용 React UI를 포함하는 Vite Entry와 6개 Windows Native Route를 추가했다.
  - Tauri `2.11.4` Rust Shell, fail-close CSP, 빈 Permission Capability, NSIS Bundle 설정을 추가했다.
  - 600px 이하 Navigation에 `flex-wrap: wrap`, `overflow-x: hidden`, Button `flex: 1 1 auto`를 적용했다.
- Root/Gate
  - Desktop lint/type/unit/build 실제 명령과 공통 Quality Gate Capability를 등록했다.
  - Vite·Tauri 정확 Pin과 Lockfile을 갱신했다.
- 검증·문서
  - `scripts/tests/desktop-tauri-shell.test.mjs`에 Desktop 계약과 500px Overflow 회귀 테스트를 추가했다.
  - `docs/01_architecture/windows_tauri_shell_contract.md`를 작성했다.
  - `docs/03_evidence/release_1/R1-M3-02/`에 Build·Installer·Navigation·Lifecycle·Gate·Screenshot·Manifest를 작성했다.
  - 생성 과정에서 갱신된 기존 R1-M1-04·R1-M1-05 Evidence 4개는 현재 결과를 R1-M3-02로 복사한 뒤 Git 기준선으로 원복했다.

### TDD

1. RED
   - 명령: `node --test scripts/tests/desktop-tauri-shell.test.mjs`
   - 결과: `5/6 PASS`, `1 FAIL`, Exit `1`
   - 원인: 600px 이하 Media Rule에 Navigation Wrap·수평 Scroll 방지·Button 유연 폭 계약이 없었다.
2. GREEN
   - 같은 명령 결과: `6/6 PASS`, Exit `0`
   - 구현: Desktop 전용 CSS 3개 선언만 추가했다.

### Build·자동 검증

| 구분 | 결과 |
| --- | --- |
| Desktop 전용 Test | `6/6 PASS` |
| Desktop Vite Build | PASS · 42 modules |
| Tauri NSIS Production Build | PASS · Release optimized · NSIS x64 |
| 전체 순차 회귀 | `209/209 PASS` |
| Workspace Lint | `11 files PASS` |
| 공통 Quality Gate | 7범주 PASS · failures `0` |
| `git diff --check` | Exit `0`, CRLF 경고 외 오류 0 |

### 실제 설치 앱 검증

- 앱: `com.daon.user`, Window ID `34736626`
- 500×900 목표 / 외곽 `502×931`
  - Navigation 2줄 Wrap
  - Home·Workspace·Notifications·Account·Organization·Operations 6개 전부 표시
  - 수평 Scrollbar 없음
  - 6개 Button을 실제 클릭하고 동일 이름의 Accessibility Region 전환 확인
- 약 800×900 / 외곽 `793×931`: 배치 유지, 수평 Scrollbar 없음
- 1200×900 / 외곽 `1196×931`: 배치 유지, 수평 Scrollbar 없음
- 최대 창 / 외곽 `2304×1392`: 1920 기준 이상 배치 유지, 수평 Scrollbar 없음
- `Alt+F4` 정상 종료 후 App·자식 Process·App 소유 Listener `0건`

어울2 Windows Helper가 창 활성화 단계에서 장기 정체되어 같은 호출을 반복하지 않았다. 최종 L4 화면 검증은 어울1이 동일 재Build·재설치 App에서 독립 수행했으며 원본 Screenshot 4개를 Evidence에 저장했다.

### Installer·정리

- Installer: `1366710 bytes`
- Installer SHA-256: `467A84D88046CF59BF318C0DD7859B5763672EEA5A7CDE4E3A2A2ABE222E5C6E`
- 서명: `NotSigned` → `unsigned_development`
- 설치 EXE: `4066304 bytes`
- 설치 EXE SHA-256: `0E3DD727D4BAF190DBD51CD37F380DBC0E8D234ADCFA3976852CE57BC1B9E7D2`
- Silent 설치 Exit `0`, 전용 Uninstaller 제거 Exit `0`
- 설치 디렉터리·동명 Registry·관련 Process·외부 Cargo Target 잔존 `0`
- 사용자 자료·다른 App 변경 `0`

## 영향 범위와 기존 기능 유지

- 수정 영향은 Desktop Shell 표현과 그 검증·계약·품질 Capability에 한정했다.
- 6개 Route·Label·Component·상태 저장 구조를 변경하지 않았다.
- `packages/ui`, Navigation/Screen/Design Token 정본과 `apps/web`, Service, API·데이터 계약을 수정하지 않았다.
- Browser/API 주소, Local Service·IPC·Loopback·외부 효과를 추가하지 않았다.

## 미해결 사항

- Installer 서명·Update·Rollback은 승인 계획대로 M9 `TS-OPS-041`까지 `deferred`다.
- Local Service·IPC·Loopback·App Instance 인증은 `R1-M3-03` 소유이며 이번 작업에서 구현하지 않았다.
- 위 항목은 R1-M3-02 합격 조건의 미진이 아니라 명시된 후속 범위다.

## 증거

- `docs/03_evidence/release_1/R1-M3-02/evidence-manifest.json`
- `docs/03_evidence/release_1/R1-M3-02/desktop-shell-build.json`
- `docs/03_evidence/release_1/R1-M3-02/installer-validation.json`
- `docs/03_evidence/release_1/R1-M3-02/app-navigation-validation.json`
- `docs/03_evidence/release_1/R1-M3-02/app-process-lifecycle.json`
- `docs/04_test_reports/release_1/R1-M3-02_progress.md`

Commit·Push·PR·Merge·외부 배포는 수행하지 않았다.

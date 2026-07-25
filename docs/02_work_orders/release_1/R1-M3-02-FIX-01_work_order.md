# R1-M3-02-FIX-01 수정 작업지시서

- 원 작업: `R1-M3-02`
- Issue ID: `R1-M3-02-GUI-500-OVERFLOW`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-23
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정

Windows Tauri Shell의 설치·실행·6개 주 탐색 경로와 1200×900·800×900 배치는 합격 후보이나, 500×900 실제 Window에서 상단 주 탐색이 가로로 넘쳐 Window 전체에 가로 Scrollbar가 발생한다. 원 작업의 `가로 Overflow 0` 합격 조건을 충족하지 못하므로 이 결함만 별도 수정한다.

이 판정은 개발자의 정식 `FAILURE_REPORT`가 아니라 어울1의 실제 설치 앱 검증에서 발견한 C1 결함이다. 실패 횟수에는 누적하지 않는다.

## 2. 재현 증거

1. 설치된 `Daon 사용자 프로그램 0.1.0`을 실행한다.
2. Window Content 기준 500×900이 되도록 외곽 Window를 약 502×931로 조절한다.
3. Home 상단의 `Home · Workspace · Notifications · Account · Organization · Operations` 주 탐색을 확인한다.
4. 주 탐색 아래에 수평 Scrollbar가 나타나고 오른쪽 `Operations`가 잘린다.
5. 같은 빌드의 1200×900과 약 800×900에서는 이 수평 Scrollbar가 나타나지 않는다.

## 3. 수정 범위

- 500×900에서 주 탐색과 페이지 Root가 Window 폭을 넘지 않게 한다.
- 6개 주 탐색 항목은 숨기거나 삭제하지 않고, 접근 가능한 Wrap·Menu·동등한 반응형 표현으로 모두 사용할 수 있어야 한다.
- 500×900에서 Root·Header·Main·Navigation에 가로 Overflow가 없어야 한다.
- 800×900·1200×900·1920×1080의 기존 배치와 12px 기준 Typography를 유지한다.
- Home·Workspace·Notifications·Account·Organization·Operations의 Route와 상태 보존을 유지한다.
- 화면 전용 수정으로 한정한다. Tauri Rust, Installer, IPC, Local Service, 공개 API, 데이터 계약, 품질 Gate 구조는 변경하지 않는다.
- 관련 없는 리팩터링·의존성·설정 변경을 금지한다.

## 4. TDD 및 검증

1. 기존 `scripts/tests/desktop-tauri-shell.test.mjs` 또는 동일 책임의 테스트에 500px 주 탐색 Overflow 회귀 사례를 먼저 추가해 RED를 확인한다.
2. 최소 CSS/Component 수정 후 GREEN을 확인한다.
3. 다음을 모두 실행하고 결과를 진행 기록에 남긴다.
   - Desktop Shell 단위/계약 테스트
   - Desktop Vite Build
   - 전체 Workspace Test·Lint
   - 공통 Quality Gate
4. 설치형 앱을 다시 Build·설치하고 실제 500×900, 800×900, 1200×900, 1920×1080에서 검증한다.
5. 500×900 실제 화면에서 6개 주 탐색이 모두 사용 가능하고 수평 Scrollbar가 없음을 Screenshot과 측정값으로 증명한다.
6. 수정 파일·영향 범위·기존 기능 유지 여부를 `판정 → 판단 이유 → 조치` 형식으로 보고한다.

## 5. 진행 기록과 종료 조건

- 착수, RED, 구현, GREEN, Build, 설치 앱 실제 검증, 전체 회귀검증, 정리, 종료 직전에 `docs/04_test_reports/release_1/R1-M3-02_progress.md`를 갱신한다.
- 오류가 발생하면 명령·원인·복구·다음 작업을 즉시 기록한다.
- 생성된 이전 작업 Evidence 파일의 불필요한 변경을 원복하고, R1-M3-02 전용 Evidence만 남긴다.
- 설치 테스트가 끝나면 테스트 앱·프로세스·외부 Cargo Target을 정리하되 사용자 자료나 다른 앱은 건드리지 않는다.
- Commit·Push는 하지 않는다. 어울1이 최종 Diff·증거를 검토한 뒤 수행한다.

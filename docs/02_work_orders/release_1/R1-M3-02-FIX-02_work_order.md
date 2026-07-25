# R1-M3-02-FIX-02 수정 작업지시서

- 원 작업: `R1-M3-02`
- 선행 수정: `R1-M3-02-FIX-01`
- Issue ID: `R1-M3-02-REVIEW-REPRO-L4`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-23
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정

제품 코드·Tauri 보안 경계·500px 수정은 합격 후보이나 독립 코드리뷰에서 다음 Important 2건이 확인됐다.

1. `verify:desktop-type`이 호출자 수동 `CARGO_TARGET_DIR` 주입 없이 실행되면 저장소 내부 Cargo Target을 만들고, 공통 보안 Scan이 공급망 Binary를 App Source로 읽어 Gate 재현성을 깨뜨린다.
2. 설치 App Evidence가 6개 Route와 500·800·1200·최대 창은 증명하지만, 작업지시서의 정확한 네 Window 크기, Keyboard/Focus/Tooltip/Escape/ARIA, 오류·`unavailable`, App 소유 Listener·자식 Process·원격 Content 부재, PNG 형식까지 완전하게 증명하지 못했다.

이는 기능 범위 변경이나 개발자의 정식 실패보고가 아니라 어울1 Merge 전 검토에서 발견한 C1 합격조건 미충족이다. 실패 횟수에는 누적하지 않는다.

## 2. 수정 범위 A — Cargo·Gate 자체 재현성

- 수동 환경변수 없이 저장소 정본 명령만으로 Desktop Cargo Check와 공통 Quality Gate가 재현돼야 한다.
- 교차 플랫폼 Node Wrapper가 `os.tmpdir()` 아래에 충돌 없는 전용 Cargo Target을 생성하고, 성공·실패 모두에서 정확한 생성 경로만 정리하도록 한다.
- `verify:desktop-type`은 Wrapper를 사용한다.
- Wrapper는 Cargo Exit Code와 Signal을 보존하고, App Source·저장소 내부에 `target` 또는 공급망 Binary를 만들지 않는다.
- 임의 Shell 문자열 결합, 광범위 Temp 삭제, 사용자 전역 Cargo/Rust 설정 변경을 금지한다.
- Installer Build는 외부 Target 위치를 명시적으로 반환하거나 기록하는 재현 가능한 저장소 명령을 제공한다. Installer Hash 수집 전에 산출물을 지우지 않고, 호출자가 지정하지 않아도 저장소 내부 Target을 만들지 않아야 한다.
- TDD로 다음을 먼저 RED로 만든다.
  - Root Script가 격리 Wrapper를 사용
  - 수동 `CARGO_TARGET_DIR` 없이 Desktop Type/Gate 실행
  - 실행 뒤 `apps/desktop/src-tauri/target`과 Root `target` 부재
  - 실패 Exit Code 전파와 정확 경로 정리

## 3. 수정 범위 B — 실제 설치 App L4 검증 보강

동일 새 Installer를 설치해 실제 Release App에서 검증한다.

- 정확한 Content 목표 `1920×1080`, `1200×900`, `800×900`, `500×900`을 사용한다. Windows Frame을 포함한 외곽 측정값과 Content 목표의 차이를 Evidence에 함께 기록한다.
- Windows API 등으로 창 크기를 설정할 경우 대상 PID·Window Handle·정확한 폭·높이만 조작하고 다른 Window는 건드리지 않는다.
- 네 크기에서 상태 보존과 가로 Overflow 0을 실제 관찰한다.
- 500×900에서 6개 주 탐색을 실제 클릭하고 각 Accessibility Region 전환을 확인한다.
- Keyboard Tab 이동, Focus 표시, `i` 설명 Tooltip 열기, Escape 닫기, ARIA Role·Name·상태를 실제 관찰한다.
- 실제 App에서 `error`와 `unavailable` 상태를 열고 정상 성공처럼 보이지 않음을 확인한다.
- App PID와 자식 Process Tree, App 소유 TCP Listener·외부 Interface Listener, Local Service·Loopback Server, 원격 Content·Dev Server 연결을 실제 실행 중 확인한다.
- Release Build에서 Console/DevTools가 의도적으로 관찰 불가하면 PASS로 추론하지 않는다. `not_observable_in_release_build`로 기록하고, 대신 실제 Process·Listener·Source/Config Scan 증거를 분리한다.
- 종료 후 Window·App·자식 Process·App 소유 Port 0, 같은 설치 App 재기동 후 Home·Workspace 확인, 최종 제거 후 Registry·설치 경로·Process·Port 0을 확인한다.
- 핵심 네 크기와 `unavailable` 상태를 PNG로 저장한다. 캡처 Tool이 JPEG를 반환하면 Byte 변조 없는 원본을 보존하고 재현 가능한 형식 변환으로 PNG를 생성하며 원본/변환 Hash·Byte를 기록한다.

## 4. 수정 범위 C — Commit·Evidence 최소화

- Windows NSIS에 필요한 Icon과 재생성 원본만 보존한다. Tauri CLI가 만든 Android·iOS·macOS 전용 Icon이 Windows Build 입력이 아니면 제거한다.
- `evidence-manifest.json`의 Source Artifact에 실제 Build 입력 전체를 포함한다. 최소한 `build.rs`, `src/main.rs`, `app-icon.svg`, 실제 Windows `icon.ico`, Tauri Config·Capability·Cargo Lock/Manifest, Desktop Entry/CSS와 Wrapper/Test를 포함한다.
- 생성 Schema 등 공급망 산출물은 Commit하지 않는다. 보존이 필요하면 Source Artifact와 Generated Artifact를 분리하고 이유를 기록한다.
- Manifest의 모든 Hash·Byte를 재계산하고 불일치 0건을 검증한다.
- 완료 판정은 실제로 수행한 항목만 `COMPLETED`로 기록한다. 관찰하지 않은 Console/Network를 추론으로 PASS 처리하지 않는다.

## 5. 최종 검증

- 전용 RED→GREEN
- 수동 `CARGO_TARGET_DIR` 없이 `npm run verify:desktop-type`
- 수동 `CARGO_TARGET_DIR` 없이 `npm run verify:quality-gate`
- 전체 순차 테스트, Workspace Lint, Desktop Production Build, Toolchain, Independence
- 격리 Installer Production Build·설치·정확 네 크기·접근성·상태·Process/Listener·재기동·제거
- `git diff --check`
- 이전 R1-M1 Evidence Dirty 0
- 저장소 내부/외부 검증 Cargo Target·설치·Registry·Process·Port 잔존 0
- Evidence JSON Parse, Manifest Hash·Byte 불일치 0

최종 검증이 생성한 이전 Evidence 변경은 R1-M3-02 Evidence를 갱신한 뒤 해당 생성 파일만 기준선으로 원복한다.

## 6. 종료 조건

- 각 단계와 오류·복구·테스트를 진행 복구 기록에 즉시 남긴다.
- 정식 결과보고 `docs/02_work_orders/reports/R1-M3-02_attempt-2.md`를 작성한다.
- 보고는 `판정 → 판단 이유 → 조치` 순서로 작성한다.
- Commit·Push·PR·배포는 수행하지 않는다.

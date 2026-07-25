# R1-M3-02-FIX-06 수정 작업지시서

- 원 작업: `R1-M3-02`
- 선행 수정: `R1-M3-02-FIX-01`~`FIX-05`
- Issue ID: `R1-M3-02-TAURI-CROSS-PLATFORM-ICON`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-25
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정과 서버 증거

ysna-server ARM64 Toolchain에서 새 정확 SHA `b76aa30fbc493937fba0685910f9353dffbf359d`의 전체 테스트 `214/214`, Lint, Web·Desktop Build, Production Audit 0건은 통과했다. Quality Gate의 Desktop Rust Type만 다음 오류로 실패했다.

```text
failed to open icon /workspace/apps/desktop/src-tauri/icons/icon.png:
No such file or directory (os error 2)
```

CA 번들 누락을 보정한 뒤 crates.io 다운로드와 Rust 컴파일이 정상 진행되어 위 오류가 제품 저장소의 유일한 차단 원인임을 확인했다. 현재 Tauri Bundle은 Windows `icon.ico`만 보유해 Linux/ARM64의 `tauri::generate_context!()` Type Check가 요구하는 기본 PNG가 없다.

이는 정식 실패보고가 아니며 유효한 실패 횟수는 `0회`다.

## 2. 목표와 설계 판단

- 기존 `icon.ico`의 시각 정체성을 유지하는 유효한 `apps/desktop/src-tauri/icons/icon.png`를 추가한다.
- PNG는 투명 배경을 허용하며 Tauri가 안정적으로 읽을 수 있는 정사각 RGBA 형식으로 생성한다.
- Windows NSIS 대상, Product Name, Identifier, CSP, Capability, 화면 및 Runtime 동작을 변경하지 않는다.
- Tauri 설정은 Windows Installer에 ICO를 계속 사용하면서 교차 플랫폼 Context Type Check에 PNG가 포함되도록 최소 변경한다.
- 생성 출처·치수·파일 Hash를 증거에 기록하고 임시 변환 파일은 남기지 않는다.

## 3. TDD와 구현

1. 현재 ARM64 오류와 로컬 기존 통과 경계를 Progress에 기록한다.
2. Desktop Shell 전용 테스트에 다음 행동 계약을 먼저 추가해 RED를 확인한다.
   - `icon.ico`와 `icon.png`가 모두 존재한다.
   - 두 파일이 각각 유효한 ICO·PNG Signature를 가진다.
   - PNG가 정사각형이고 Tauri 사용에 적합한 치수를 가진다.
   - Tauri Bundle Icon 계약이 Windows ICO와 교차 플랫폼 PNG를 명시한다.
3. 기존 ICO를 출처로 같은 아이콘의 PNG를 생성하고 최소 설정 변경으로 GREEN을 만든다.
4. 임시 변환 Tool·파일을 제품 의존성이나 저장소에 추가하지 않는다.
5. R1-M3-02 Generator, Source/Evidence Manifest, Progress, Attempt-2에 FIX-06과 새 Asset·서버 실패/복구 근거를 정합화한다.

## 4. 필수 검증

- 전용 RED→GREEN
- `node --test --test-concurrency=1 scripts/tests/*.test.mjs`
- `npm run lint:workspace`
- `npm run build --workspace @daon-user/web`
- `npm run build --workspace @daon-user/desktop`
- 수동 환경변수 없는 로컬 `npm run verify:desktop-type`
- `npm audit --omit=dev --audit-level=high --json`
- 수동 환경변수 없는 `npm run verify:quality-gate`
- JSON Parse, Source/Evidence Hash·Byte 불일치 `0`
- `git diff --check`
- R1-M1-05 Evidence Dirty `0`
- `gen`, Root/Desktop Cargo Target, Temp Check Target, Daon App Process `0`

모든 단계의 착수·완료·오류·복구·테스트·종료 직전에 진행 복구 기록을 갱신한다.

## 5. 제외·보호 범위

- 제품 화면·기능, 공개 API, 데이터 계약, 보안 정책, same-origin 경계를 변경하지 않는다.
- 기존 `icon.ico`를 교체하거나 Windows 설치 시각을 임의 변경하지 않는다.
- PostCSS·Next·Vite·Lockfile과 R1-M2 역사 증거를 변경하지 않는다.
- 새 Runtime/Production Dependency를 추가하지 않는다.
- 화면/App 실행, Commit, Push, PR, 서버 배포를 수행하지 않는다.

## 6. 종료 조건

- `docs/02_work_orders/reports/R1-M3-02_attempt-2.md`와 Progress를 최신 수치로 갱신한다.
- 종료 보고는 `status | issue_id | 수행한 작업 | 생성·변경 결과 | 테스트 결과 | 미해결 사항 | 다음 판단` 형식을 사용한다.


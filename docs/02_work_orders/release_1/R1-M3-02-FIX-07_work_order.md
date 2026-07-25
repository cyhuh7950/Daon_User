# R1-M3-02-FIX-07 수정 작업지시서

- 원 작업: `R1-M3-02`
- 선행 수정: `R1-M3-02-FIX-01`~`FIX-06`
- Issue ID: `R1-M3-02-CI-UBUNTU-GATE`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-25
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정과 증거

PR #16의 Release 1 Quality Gate Run `30158657253`은 Toolchain·npm ci·증거 업로드는 통과했지만 공통 Gate에서 다음 두 범주가 실패했다.

1. `type/desktop-rust-type`: GitHub Ubuntu Runner에 Tauri Linux Type Check용 시스템 라이브러리 설치 단계가 없다.
2. `independence/repository-independence`: `scripts/generate-r1-m3-02-evidence.mjs`가 ysna-server 외부 절대 배포 경로를 Source 상수로 보유한다. PR Merge Worktree 재현 결과 정확한 위반은 `PATH_EXTERNAL_ABSOLUTE` 1건이다.

로컬 Windows와 ysna-server ARM64 Gate는 통과했으나 CI Ubuntu Gate가 실패했으므로 Merge할 수 없다. 정식 실패보고가 아니며 유효한 실패 횟수는 `0회`다.

## 2. 목표와 설계 판단

- GitHub Workflow가 승인 Rust Pin을 사용하는 Desktop Type Check를 위해 Tauri 공식 Linux 필수 패키지를 Gate 전에 설치한다.
- 허용 Package는 최소 `libwebkit2gtk-4.1-dev`, `libappindicator3-dev` 또는 Ubuntu의 동등 Ayatana Package, `librsvg2-dev`, `patchelf`, `ca-certificates`, `pkg-config`로 제한한다.
- `sudo apt-get update`와 `sudo apt-get install --yes --no-install-recommends ...`를 명시 Step으로 사용하고 Quality Gate 완화·우회·N/A 전환을 금지한다.
- Generator는 외부 절대 배포 경로를 Runtime/Source 상수로 소유하지 않는다. 서버 위치는 정형 증거 `docs/03_evidence/release_1/R1-M3-02/server-validation-manifest.json`만 참조하고 Generator 계약에는 저장소 상대 증거 경로를 기록한다.
- 이미 검증된 서버 Manifest의 관측 사실과 exact implementation SHA `0a4c76b1ba9c165bd0adfbcd62dccdabc8f716d5`는 변경하지 않는다.

## 3. TDD와 구현

1. PR Merge Worktree의 Independence 1건과 CI Gate 실패를 Progress에 RED로 기록한다.
2. Workflow 계약 테스트를 먼저 추가해 다음을 검증한다.
   - Tauri Linux Prerequisites Step이 npm ci와 공통 Gate보다 앞선다.
   - 허용 Package가 명시되고 Gate Command는 변경되지 않는다.
   - Rust Pin은 기존 `rust-toolchain.toml`/`toolchain-versions.json` 정본을 계속 사용한다.
3. Generator/Independence 테스트는 외부 절대 경로가 Source에 재도입되면 실패하게 한다.
4. 최소 Workflow·Generator 변경으로 RED를 GREEN으로 만든다.
5. 서버 Manifest·Summary는 관측 정본으로 보존하고 R1-M3-02 Source/Evidence Manifest·Progress·Attempt-2만 최신화한다.

## 4. 필수 검증

- 전용 RED→GREEN
- `npm run verify:independence -- --no-write` 위반 `0`
- `node --test --test-concurrency=1 scripts/tests/*.test.mjs`
- `npm run lint:workspace`
- `npm run build --workspace @daon-user/web`
- `npm run build --workspace @daon-user/desktop`
- 로컬 `npm run verify:desktop-type`
- `npm audit --omit=dev --audit-level=high --json`
- 로컬 `npm run verify:quality-gate` 7범주 PASS
- JSON Parse, Source/Evidence Hash·Byte 불일치 `0`
- `git diff --check`, R1-M1-05 Dirty `0`, 잔존물 `0`

## 5. 제외·보호 범위

- 제품 기능·화면·API·데이터·보안 정책·same-origin·Tauri Runtime 코드를 변경하지 않는다.
- Package/Lockfile/PostCSS/Next/Vite 버전을 변경하지 않는다.
- Quality Gate 정책과 필수 Check를 완화하거나 CI 전용 Skip/예외를 추가하지 않는다.
- 서버 검증 사실과 R1-M2 역사 증거를 바꾸지 않는다.
- 화면/App, Commit, Push, PR 조작, 서버 명령·배포를 수행하지 않는다.

## 6. 종료 조건

- Progress와 `docs/02_work_orders/reports/R1-M3-02_attempt-2.md`를 최신화한다.
- 종료 보고는 `status | issue_id | 수행한 작업 | 생성·변경 결과 | 테스트 결과 | 미해결 사항 | 다음 판단` 형식을 사용한다.


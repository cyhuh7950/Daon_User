# 작업지시서 `R1-M1-03`

## 1. 문서 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M1-03` |
| 버전 / SHA-256 | `1.0` / 작업지시 프롬프트와 Attempt Ledger에 고정 |
| issue_id | `R1-M1-03-I001` |
| 상태 / 시도 | `READY` / `1` |
| 단일 Writer | 어울2 · `daon-developer` |
| 선행조건 | `R1-M1-02 COMPLETED` |
| 선행 Evidence | `docs/03_evidence/release_1/R1-M1-02/manifest.json` |
| 기준 Branch / Commit | `codex/r1-m1-03` / `88091b7ec50f6f01285d8f0fa75df93b81eef09e` |
| 상세 설계 정본 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` · v0.6 · `7FC4BCE7B517E915520F587D812A241E59F6C8B492671B6C8A4BC53140393C31` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` · v0.8 · `790BD4C2AC708328FB4790CBA2842DFEB62B8B1E1093BAA4D6FA652F6B4F70EF` |
| 승인 기준 Manifest | `docs/02_work_orders/release_1_baseline_manifest.json` · `1F263770969EF3D392E051C29DFBDEA51F73C2B0CAFE00E016EC273B85E894DB` |
| 기술 정정 | `CHG-R1-M1-03-001` · C1 · 제품 계약 변경 없음 |
| 진행 복구 기록 | `docs/04_test_reports/release_1/R1-M1-03_progress.md` |
| 결과보고서 | `docs/02_work_orders/reports/R1-M1-03_attempt-1.md` |

작업자는 `AGENTS.md`, 승인 문서와 이 작업지시서를 EOF까지 읽고 Hash를 확인한 뒤 시작한다. 요약본은 정본을 대체하지 않는다. 실제 저장소 상태가 계획과 다르면 증거를 남기고 승인 경계를 넘지 않은 상태에서 어울1에게 보고한다.

## 2. 목표와 범위

- 단일 목표: 개발·CI·새 환경이 동일한 정확 버전과 Lockfile을 사용하도록 Monorepo Toolchain·Dependency 기준선을 기계적으로 고정한다.
- 사용자 관점 완료 조건: 개발자의 전역 환경에 의존하지 않고 저장소 버전 파일과 Lockfile만으로 동일 의존성 집합을 재현할 수 있다.
- 포함:
  - Node/npm/Corepack, Python/uv, Rust, React Native, Tauri CLI, PostgreSQL, Xcode, CocoaPods와 Web 공통 도구의 정확 버전 파일
  - npm Workspace와 `package-lock.json`
  - uv Workspace와 `uv.lock`
  - 기계 판독 Toolchain Manifest와 검증 Script
  - 개발자·CI가 사용하는 설치·검증 절차 문서
  - 격리된 임시 Toolchain/Cache를 사용한 정확 버전 존재·Lockfile 재현 검증
- 제외:
  - Web·Desktop·Mobile 실행 화면과 Framework Source Scaffold
  - Tauri Rust App·FastAPI Endpoint·DB Schema·Runtime Service 구현
  - CI Workflow와 독립성 검사(`R1-M1-04`, `R1-M1-05`)
  - 사용자 전역 Toolchain 변경, 서비스 설치, WSL/외부 배포, Commit·Push·PR
- 변경 허용 경로:
  - 루트 `.node-version`, `.python-version`, `.npmrc`, `.tool-versions`, `rust-toolchain.toml`, `toolchain-versions.json`, `package.json`, `package-lock.json`, `pyproject.toml`, `uv.lock`, `.postgres-version`, `.xcode-version`, `.cocoapods-version`, `.gitignore`
  - `apps/web/package.json`, `apps/desktop/package.json`, `apps/mobile/package.json`
  - `packages/ui/package.json`, `packages/contracts/package.json`, `packages/design-tokens/package.json`
  - `services/api/pyproject.toml`, `services/local-service/pyproject.toml`
  - `scripts/verify-toolchain-baseline.mjs`
  - `docs/01_architecture/toolchain_dependency_baseline.md`
  - `docs/04_test_reports/release_1/R1-M1-03_progress.md`
  - `docs/02_work_orders/reports/R1-M1-03_attempt-1.md`
  - `docs/03_evidence/release_1/R1-M1-03/manifest.json`
- 변경 금지 경로: 위 허용 경로 이외 전체. 승인 설계·계획·결정·Baseline Manifest·선행 Evidence·`AGENTS.md`·`.agents/`·`.codex/`는 수정하지 않는다.

다른 작업자의 변경을 되돌리거나 정리하지 않는다. 실행 코드가 없는 Component에 성공하는 척하는 가짜 Build Script를 만들지 않는다. 실제 기본 Build는 `R1-M1-05`와 M1 Exit Gate에서 검증하며, 이번 작업은 정확 버전과 Clean Dependency Resolution의 재현성을 증명한다.

## 3. Toolchain·Dependency 계약

### 3.1 정확 버전

| 영역 | 정확 버전 |
| --- | --- |
| Node.js / npm / Corepack | `24.18.0` / `11.12.1` / `0.35.0` |
| Python / uv | `3.14.3` / `0.11.2` |
| Rust | `1.97.1` |
| Tauri CLI | `2.11.4` |
| React Native | `0.86.0` |
| PostgreSQL | `18.4` |
| Xcode / CocoaPods | `26.6` / `1.16.2` |
| Next.js / React / TypeScript | `16.2.10` / `19.2.7` / `7.0.2` |

버전 범위 기호 `^`, `~`, `*`, `latest`, `x`를 사용하지 않는다. `package-lock.json`은 npm `11.12.1` 기준으로 생성하고 `lockfileVersion`과 Workspace Package를 검증한다. `.npmrc`는 최소 `save-exact=true`, `engine-strict=true`를 포함한다.

### 3.2 Workspace 경계

- npm Workspace는 `apps/*`, `packages/*`만 포함한다. `services/*`는 Python uv Workspace가 소유한다.
- Root와 각 JavaScript Package는 `private: true`를 사용한다.
- `apps/web`은 Next.js·React, `apps/desktop`은 Tauri CLI와 공용 React UI 계약, `apps/mobile`은 React Native·React의 정확 버전을 선언한다.
- `packages/ui`는 React를 Peer 계약으로 두고 Contracts·Design Tokens만 참조할 수 있다.
- `packages/contracts`, `packages/design-tokens`는 Runtime 외부 의존이 없는 Leaf다.
- Root `pyproject.toml`은 `services/api`, `services/local-service`만 uv Workspace Member로 포함하며 Python `==3.14.3` 계약을 사용한다.
- 아직 승인되지 않은 Framework·Provider·DB Client 의존성을 선행 추가하지 않는다.

### 3.3 재현·안전 계약

- 검증 도구는 `toolchain-versions.json`, 버전 파일, Package Manifest, Lockfile의 상호 일치를 검사하고 불일치 시 0이 아닌 Exit Code를 반환한다.
- 사용자 전역 npm·Python·Rust 환경을 설치·업데이트하지 않는다. 필요한 Download와 Cache는 `C:\tmp` 아래의 작업 전용 경로를 사용하고 저장소에 Commit하지 않는다.
- Package Lifecycle Script는 실행하지 않는 Clean Install 검증을 우선한다. 외부 Package Script 실행이 필요하면 중단하고 근거를 보고한다.
- Windows에서 검증할 수 없는 Xcode·CocoaPods Runtime은 정확 Pin과 문서 계약만 검사하고 `EXTERNAL_BLOCKED`를 숨기지 않는다.
- 개발·테스트 명령은 증거일 뿐 사용자·운영자가 Python·DB CLI를 직접 실행하는 제품 운영 절차로 만들지 않는다.

## 4. 단계와 복구 기록

| 단계 | 작업 | 단계 완료조건 |
| --- | --- | --- |
| S0 | 정본 EOF·Hash·선행 Evidence·Branch·Dirty·단일 Writer 확인 | 시작 Snapshot과 적용 조항 기록 |
| S1 | 레지스트리·배포 채널 정확 버전과 기존 Runtime 확인 | 버전별 확인 결과와 외부 차단 기록 |
| S2 | 버전 파일·Workspace Manifest·검증 Script 작성 | 모든 정확 Pin이 단일 Manifest와 일치 |
| S3 | npm·uv Lockfile 생성 | Workspace 전체가 정확 버전으로 해석됨 |
| S4 | 격리 Cache에서 정적 검증·Clean Resolution 재실행 | Script 성공, npm Clean Install 성공, uv Lock 검증 성공 |
| S5 | Diff·무관 변경·완료조건 최종 대조, Evidence·결과보고 작성 | 허용 범위 밖 Diff 0, 종료 Snapshot |

`docs/02_work_orders/templates/progress_template.md`를 사용해 지정 진행 파일을 착수, 각 단계 완료, 오류·복구, 각 테스트, 결과보고 제출과 종료 직전에 즉시 갱신한다. 필수 필드는 `recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`다.

## 5. 테스트와 완료조건

### 필수 자동·정적 검증

- 승인 설계·계획·Manifest·작업지시서 SHA-256 일치
- `git branch --show-current`가 `codex/r1-m1-03`
- `git merge-base --is-ancestor 88091b7ec50f6f01285d8f0fa75df93b81eef09e HEAD`
- `node --version`, `npm --version`, `corepack --version`이 승인 Pin과 일치
- `node scripts/verify-toolchain-baseline.mjs` 성공
- 임시 npm Cache를 사용한 `npm ci --ignore-scripts` 성공과 Workspace Dependency Tree 오류 0건
- 임시 uv Cache·Python 설치 경로를 사용한 Python `3.14.3` Lock 생성·`uv lock --check` 성공
- Rust `1.97.1` 배포 채널 존재 확인과 `rust-toolchain.toml` 일치. 격리 설치가 안전하게 가능하면 `rustc 1.97.1`도 실행하고, 불가능하면 이유를 명시한다.
- npm Manifest의 범위 버전 0건, `package-lock.json`과 선언 버전 불일치 0건
- `git diff --check`, 추적 파일 삭제 0건, 허용 경로 밖 변경 0건

### 완료조건

- R1-D002 정확 버전이 결정 기록·기계 판독 Manifest·각 버전 파일·Lockfile에서 일치한다.
- npm과 uv Workspace가 각각의 소유 경계를 침범하지 않는다.
- 새 환경의 Clean Dependency Resolution이 격리 Cache에서 재현된다.
- 사용자 전역 Toolchain·서비스·설정을 변경하지 않았다.
- Xcode·CocoaPods의 Windows 미검증 상태를 성공으로 오인하지 않고 외부 차단으로 기록했다.
- 가짜 App Build나 승인 전 Runtime Dependency가 추가되지 않았다.
- 수행 명령·Exit Code·SHA-256·환경·제한 사항이 Evidence Manifest에 기록된다.

## 6. 결과보고 계약

결과는 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나로 제출한다. `docs/02_work_orders/templates/work_report_template.md`를 사용해 다음 필드를 빠짐없이 포함한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

첫 오류만으로 실패보고하지 말고 원인·대안·현재 Diff·테스트를 조사한다. 승인 범위 변경이 필요하면 구현하지 말고 증거와 선택지를 보고한다.

- 중대한 미진: Pin/Lock 불일치, Clean Resolution 실패, 전역 환경 변경, 승인되지 않은 Dependency 추가, 허용 범위 밖 변경, 필수 증거 부재
- 경미 보완: 완료조건을 깨지 않는 문구·표시·증거 정리
- 사소한 보완만으로 합격 작업 전체를 다시 열지 않는다.

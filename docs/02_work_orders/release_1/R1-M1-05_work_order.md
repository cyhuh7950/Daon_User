# 작업지시서 `R1-M1-05`

## 1. 문서 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M1-05` |
| 버전 / SHA-256 | `1.0` / 작업지시 프롬프트와 Attempt Ledger에 고정 |
| issue_id | `R1-M1-05-I001` |
| 상태 / 시도 | `READY` / `1` |
| 단일 Writer | 어울2 · `daon-developer` |
| 선행조건 | `R1-M1-03 COMPLETED`, `R1-M1-04 COMPLETED` |
| 선행 Evidence | `R1-M1-03 manifest` · `4173AFEDDD6456F617E4DEA8BD78F4DDBE195F2364A69EB80590AADC736B483F`; `R1-M1-04 manifest` · `3B1D7A32F2B9FD13555ED3A5366EAB608F13E6209E15BBE695BE2699FA17710A` |
| 기준 Branch / Commit | `codex/r1-m1-05` / `707871b8779ee5b1959fa85f9b76897cf2d5b39e` |
| 상세 설계 정본 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` · v0.7 · `3317B404F9FD4A2AFFE3A15EBBF456DE0B88AE56249666872C09629059C82038` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` · v0.9 · `30BE3B078804A73F5DBF83B1780DB93665199DA9AB12473E306F2E5F054996A3` |
| 승인 기준 Manifest | `docs/02_work_orders/release_1_baseline_manifest.json` · `251F420DA2C99AEE94788E220FF92B726D9FD8616A2F6FD8EFD8599D93122566` |
| ysna 승인 기록 | `APR-DEVENV-YSNA-20260720-01` · `480F69E636569FC0158C5527DB2FAD5EA6B81EAECBBF47252C36F998A4DF3A3A` |
| 진행 복구 기록 | `docs/04_test_reports/release_1/R1-M1-05_progress.md` |
| 결과보고서 | `docs/02_work_orders/reports/R1-M1-05_attempt-1.md` |

작업자는 `AGENTS.md`, 승인 문서와 이 작업지시서를 EOF까지 읽고 Hash를 확인한 뒤 시작한다. 요약본은 정본을 대체하지 않는다. 실제 저장소 상태가 계획과 다르면 증거를 남기고 승인 경계를 넘지 않은 상태에서 어울1에게 보고한다.

## 2. 목표와 범위

- 단일 목표: 로컬과 GitHub CI가 동일한 품질 계약을 실행하고, 병합 대상 Git SHA를 `ysna-server`의 격리 경로에서 재검증해야만 Merge 가능한 통합 Gate를 만든다.
- 사용자 관점 완료 조건: Lint·Type·Unit·Contract·Build·보안·독립성 중 필수 검사가 실패하거나 승인된 서버 검증 증거가 없으면 합격으로 보고되지 않는다.
- 포함:
  - 기계 판독 품질 Gate Policy와 공통 Runner
  - 저장소 성숙도에 따라 필수 검사를 판정하는 명시적 Capability Matrix
  - GitHub Actions Workflow와 결과 Artifact
  - 로컬 Gate 자동 테스트와 실제 저장소 실행
  - 불변 Git SHA 기반 `ysna-server` 격리 배포·테스트 계약 및 증거
  - CI·서버 검증 운영 문서, 진행 기록, Evidence Manifest, 결과보고
- 제외:
  - 제품 화면·API·DB Schema·Migration·Connector·BFF 구현
  - Web·Tauri·React Native·Python Runtime Source Scaffold 추가
  - Package Version·Toolchain Pin·Lockfile 변경
  - 기존 GitHub 저장소 Branch Protection의 관리자 설정 변경
  - WSL 필수 배포, Oracle Cloud 운영 배포
- 변경 허용 경로:
  - `package.json`의 품질 Gate Script 항목
  - `quality-gate-policy.json`
  - `.github/workflows/release-1-quality-gate.yml`
  - `scripts/lib/quality-gate.mjs`
  - `scripts/verify-quality-gate.mjs`
  - `scripts/tests/quality-gate.test.mjs`
  - `docs/01_architecture/ci_quality_gate_contract.md`
  - `docs/04_test_reports/release_1/R1-M1-05_progress.md`
  - `docs/02_work_orders/reports/R1-M1-05_attempt-1.md`
  - `docs/03_evidence/release_1/R1-M1-05/**`
- 변경 금지 경로: 위 허용 경로 이외 전체. 특히 `package-lock.json`, `uv.lock`, Toolchain Pin, App·Service·Package Source, 승인 설계·계획·결정·Baseline Manifest·선행 Evidence·`AGENTS.md`·`.agents/`·`.codex/`는 수정하지 않는다.

다른 작업자의 변경을 되돌리거나 정리하지 않는다. Git·설치·서버 명령은 작업자 경합을 고려해 충분히 기다리고, 단순 60초 경과만으로 실패나 무진행으로 판정하지 않는다.

## 3. 품질 Gate 계약

### 3.1 판정 모델

- 각 Capability는 `PASS`, `FAIL`, `NOT_APPLICABLE_FOUNDATION_ONLY` 중 하나로 판정한다.
- `NOT_APPLICABLE_FOUNDATION_ONLY`는 Policy에 선언된 정확한 파일·Script 부재 조건을 모두 만족할 때만 허용한다. 단순 미구현·명령 누락·실행 오류를 이 상태로 바꾸지 않는다.
- 현재 Foundation 저장소는 Runtime Source를 만들지 않은 승인 단계이므로 Source 전용 Lint·Type·Unit·Contract·제품 Build는 위 조건을 충족할 때만 `NOT_APPLICABLE_FOUNDATION_ONLY`가 가능하다.
- 이후 해당 구성요소에 Runtime Source 또는 Build 설정이 하나라도 생겼는데 필수 Script·검사가 없으면 `MISSING_REQUIRED_CAPABILITY`로 `FAIL`한다.
- Toolchain, Lockfile, Workspace 경계, 독립성, Policy Schema, 기밀정보·금지 경로 정적 검사는 현재부터 항상 필수이며 생략할 수 없다.
- 전체 성공은 필수 항목 전부 `PASS`, 허용 조건을 만족한 Foundation 전용 항목만 `NOT_APPLICABLE_FOUNDATION_ONLY`, `FAIL` 0건일 때다.

### 3.2 필수 검사 범주

1. `lint`: Source 존재 시 구성요소별 Lint Script·실행 성공. Source 부재 판정 근거를 보고서에 기록.
2. `type`: TypeScript/Python Source 존재 시 Type 검사 Script·실행 성공.
3. `unit`: Test 대상 Source 존재 시 Unit Test Script·실행 성공. Gate Runner 자체 Test는 항상 실행.
4. `contract`: 공개 Contract·Schema가 생기면 Contract Test 필수. 현재 `packages/contracts`가 문서 경계만 가진 상태인지 구조 검사.
5. `build`: 현재는 Lockfile 설치 가능성, 정확 Pin, Workspace Manifest와 구성요소 Build Capability 상태를 검증한다. 제품 Source 추가 후에는 실제 Build Script 성공을 요구한다.
6. `security`: Secret 의심 패턴, 금지 외부 절대경로·내부 주소, Lockfile 무결성, 고위험 Production Dependency Audit를 검사한다. Registry/Network 불능은 성공이 아니라 별도 실패 근거로 남긴다.
7. `independence`: `npm run verify:independence`와 결과 JSON 위반 0건을 항상 요구한다.

Runner는 각 범주의 명령, 시작·종료 시각, Exit Code, 상태, 근거 파일과 SHA를 JSON으로 남기고 전체 실패 시 Exit 1, Policy/실행 불능 시 Exit 2를 사용한다. Secret·Token·개인정보와 내부 자격증명은 Log·Artifact에 남기지 않는다.

### 3.3 GitHub Actions 계약

- `pull_request`의 `codex/release-1` 대상과 `workflow_dispatch`에서 실행한다.
- `npm ci`와 고정 Toolchain/Lockfile을 사용하고 공통 Runner를 호출한다. CI만의 별도 완화 규칙을 만들지 않는다.
- 결과 JSON과 사람이 읽을 수 있는 Summary를 성공·실패 여부와 무관하게 Artifact로 보존한다.
- Workflow 권한은 최소 읽기 권한을 기본으로 하고 Repository Secret 값을 출력하지 않는다.
- Workflow Job 실패가 Required Check로 사용 가능한 고정 Job 이름을 제공한다. Branch Protection 관리자 설정은 어울1의 별도 확인 대상이며 개발자가 변경하지 않는다.
- 실제 실패 차단 증거는 Runner 음성 Test와 실패 Fixture 실행으로 남긴다. 승인 Branch에 의도적 실패 Commit을 Push하지 않는다.

## 4. ysna-server 격리 검증 계약

- 승인 흐름: `로컬 수정·기본 검증 → 어울1 검토·Commit·Git Push → ysna-server 소스 배포 → DB Migration → 서버 테스트 → PR Merge`.
- 작업자는 S5에서 구현·로컬 검증을 완료하면 진행 파일과 중간 보고에 `HANDOFF_READY`를 기록하고 모든 코드 쓰기를 중지한다. 이 시점에는 최종 `COMPLETED`를 제출하지 않는다.
- 어울1이 Diff를 검토해 Commit·Push하고 불변 전체 Git SHA를 전달한 뒤, 같은 어울2가 S6부터 재개한다. 재개 후에는 구현 코드를 수정하지 않고 서버 배포·검증·증거·최종 보고만 수행한다.
- 접속은 `ssh ysna-server`, 허용 Root는 `/home/ubuntu/deploy/daon-user`다. 정확 경로는 `/home/ubuntu/deploy/daon-user/R1-M1-05/<full_git_sha>`로 격리한다.
- 기존 `/home/ubuntu/deploy/common`, `netdata`, `proxy`, `shared-db`와 그 Container·Network·Volume·파일을 참조·변경·재시작·삭제하지 않는다.
- 배포본 `git rev-parse HEAD`는 어울1이 전달한 SHA와 완전히 같아야 한다. Branch 최신 상태나 로컬 Dirty Source를 대신 사용하지 않는다.
- 서버가 ARM64임을 확인하고 사용하는 Image·도구가 ARM64 또는 Multi-arch인지 기록한다. 기존 서비스와 Port가 충돌하지 않게 하고 이번 Source-only 검증에 불필요한 Listen Port를 열지 않는다.
- 현재 승인 기준에는 DB Schema와 Migration이 없으므로 Migration 단계는 `NOT_APPLICABLE_NO_SCHEMA`로 기록한다. Migration 경로·Schema 파일 부재를 기계적으로 확인한 근거가 있어야 하며, 이후 하나라도 생기면 Migration 실행·결과 증거 없이는 Gate를 통과할 수 없다.
- 설치·테스트는 격리 Checkout에서 고정 Lockfile로 수행한다. Host Toolchain이 승인 Pin과 다르면 임의 완화하지 말고 ARM64 호환 격리 Runner를 사용하거나 증거와 함께 보고한다.
- 서버 검증 전후 Docker Container·Network·Volume 목록과 대상 경로 상태를 비교해 기존 자원의 변경 0건을 증명한다.
- 서버 검증 필수 결과: 정확 SHA, Architecture, Toolchain/Lockfile, 공통 품질 Gate, 독립성 위반 0, Migration 상태, 기존 자원 변경 0, 명령별 Exit Code.

## 5. 단계와 복구 기록

| 단계 | 작업 | 단계 완료조건 |
| --- | --- | --- |
| S0 | 정본 EOF·Hash·선행 Evidence·Branch·Dirty·단일 Writer 확인 | 시작 Snapshot과 적용 조항 기록 |
| S1 | 현재 Source/Script/CI·Build Capability와 회귀 위험 분석, Test 우선 작성 | Foundation 허용 조건과 Source 등장 시 실패 Fixture 확정 |
| S2 | Policy·공통 Runner·Root Script·운영 문서 구현 | 7개 범주·Exit·Artifact 계약 구현 |
| S3 | GitHub Actions 작성과 정적 계약 검사 | Trigger·권한·고정 Job·공통 Runner·Artifact 일치 |
| S4 | Node Test·실제 로컬 Gate·보안·독립성·Diff 검사 | 자동 Test·실제 Gate 성공, 허용 범위 밖 Diff 0 |
| S5 | Evidence 초안·진행 파일 `HANDOFF_READY`, 어울1 중간 보고 | 코드 쓰기 중지, Commit·Push 대상 Diff 전달 |
| S6 | 어울1이 전달한 불변 SHA를 ysna-server 격리 경로에 배포·검증 | 정확 SHA·ARM64·Gate·Migration 상태·기존 자원 불변 증거 |
| S7 | 최종 Evidence Manifest·결과보고·종료 Snapshot | 완료조건 전수 대조와 `COMPLETED` 또는 정식 상태 제출 |

`docs/02_work_orders/templates/progress_template.md`를 사용해 지정 진행 파일을 착수, 각 단계 완료, 오류·복구, 각 테스트, Hand-off, 서버 검증과 종료 직전에 즉시 갱신한다. 필수 필드는 `recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`다.

## 6. 테스트와 완료조건

### 필수 자동·정적 검증

- 승인 설계·계획·Manifest·선행 Evidence·작업지시서 SHA-256 일치
- 기준 Branch와 기준 Commit 조상 관계 확인
- `node --test scripts/tests/quality-gate.test.mjs` 성공
- 정상 Foundation, Runtime Source 등장 후 필수 Capability 누락, 명령 실패, Policy 오류 Fixture의 상태·Exit Code 정확성
- `npm run verify:toolchain`, `npm run verify:independence`, `npm run verify:quality-gate` 성공
- GitHub Workflow YAML Parse와 Trigger·최소 권한·고정 Job·Artifact·공통 Runner 계약 검사
- `npm ci`가 승인 Lockfile을 변경하지 않고 성공
- `package-lock.json`, `uv.lock`, 승인 Toolchain Pin과 선행 Evidence 불변
- `git diff --check`, 추적 파일 삭제 0건, 허용 경로 밖 변경 0건
- 어울1 Push 뒤 ysna-server에서 정확 SHA와 공통 Gate 성공
- DB Migration `NOT_APPLICABLE_NO_SCHEMA` 근거 또는 실제 Migration 성공 근거
- 서버 전후 기존 Container·Network·Volume 변경 0건

### 완료조건

- 로컬과 CI가 동일 Policy·Runner·Lockfile·Toolchain 기준을 사용한다.
- 7개 품질 범주의 상태가 전부 명시되고 조용한 Skip이 없다.
- Runtime Source가 추가되면 해당 필수 검사 미구성이 실제 음성 Test에서 Gate 실패로 증명된다.
- CI Job 또는 필수 ysna-server 검증 실패 시 합격 보고를 생성하지 않는다.
- Artifact에 Git SHA·범주별 명령/상태/Exit·Hash·한계가 기록되며 Secret은 없다.
- ysna-server 격리 배포가 승인 경로·정확 SHA·ARM64·기존 자원 불변 계약을 만족한다.
- 기존 Toolchain·Lockfile·제품 Source·승인 기준선을 변경하지 않았다.

## 7. 결과보고 계약

결과는 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나로 제출한다. `HANDOFF_READY`는 S5 중간 상태이며 최종 결과가 아니다. `docs/02_work_orders/templates/work_report_template.md`를 사용해 다음 필드를 빠짐없이 포함한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

첫 오류만으로 실패보고하지 말고 원인·대안·현재 Diff·테스트를 조사한다. 승인 범위 변경이 필요하면 구현하지 말고 증거와 선택지를 보고한다.

- 중대한 미진: 필수 범주 누락·조용한 Skip, Source 등장 후 검사 미실패, CI와 로컬 규칙 불일치, Workflow가 실패를 성공 처리, 필수 Artifact·서버 정확 SHA·자원 불변 증거 부재, Lockfile/승인 경계 변경
- 경미 보완: 완료조건을 깨지 않는 문구·표시·증거 정리
- 사소한 보완만으로 합격 작업 전체를 다시 열지 않는다.

R1-M1-05 수락 후 M1 Exit에 도달하면 어울1은 테스트 계획의 `TP-2` 시점으로 전환하고 신산님에게 결과·위험과 Go/No-Go 판단을 보고한다. 신산님의 결정 전에는 M2 구현으로 진행하지 않는다.

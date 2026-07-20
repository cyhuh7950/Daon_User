# 작업지시서 `R1-M1-04`

## 1. 문서 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M1-04` |
| 버전 / SHA-256 | `1.0` / 작업지시 프롬프트와 Attempt Ledger에 고정 |
| issue_id | `R1-M1-04-I001` |
| 상태 / 시도 | `READY` / `1` |
| 단일 Writer | 어울2 · `daon-developer` |
| 선행조건 | `R1-M1-02 COMPLETED`, `R1-M1-03 COMPLETED` |
| 선행 Evidence | `docs/03_evidence/release_1/R1-M1-03/manifest.json` · `D23C2394297AD7B6BD992C6D02BC6D03FB087B7C9B6A40DCDDC786C538A14321` |
| 기준 Branch / Commit | `codex/r1-m1-04` / `02cce4bb46eaa7ea36fab7c131cd9c328df8114d` |
| 상세 설계 정본 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` · v0.6 · `7FC4BCE7B517E915520F587D812A241E59F6C8B492671B6C8A4BC53140393C31` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` · v0.8 · `790BD4C2AC708328FB4790CBA2842DFEB62B8B1E1093BAA4D6FA652F6B4F70EF` |
| 승인 기준 Manifest | `docs/02_work_orders/release_1_baseline_manifest.json` · `1F263770969EF3D392E051C29DFBDEA51F73C2B0CAFE00E016EC273B85E894DB` |
| 진행 복구 기록 | `docs/04_test_reports/release_1/R1-M1-04_progress.md` |
| 결과보고서 | `docs/02_work_orders/reports/R1-M1-04_attempt-1.md` |

작업자는 `AGENTS.md`, 승인 문서와 이 작업지시서를 EOF까지 읽고 Hash를 확인한 뒤 시작한다. 요약본은 정본을 대체하지 않는다. 실제 저장소 상태가 계획과 다르면 증거를 남기고 승인 경계를 넘지 않은 상태에서 어울1에게 보고한다.

## 2. 목표와 범위

- 단일 목표: 독립 제품 경계와 same-origin/Connector 계약 위반을 Commit 전에 기계적으로 차단하는 재현 가능한 독립성 검사 계약을 만든다.
- 사용자 관점 완료 조건: 다른 Daon 제품의 DB·서비스 URL·파일 경로·Source Import·Runtime Image·Package를 직접 참조하거나 표준 Connector를 우회하는 변경이 검사에서 실패한다.
- 포함:
  - 기계 판독 `independence-policy.json`
  - Repository Dependency Graph·Workspace Package·Source Import·URL·Path·Runtime Image·Connector 경계 검사
  - 실제 저장소를 검사하는 CLI와 위반 유형별 양성·음성 자동 테스트
  - Browser 실행 후보와 Server/BFF 파일을 구분한 same-origin 정적 검사 계약
  - JSON Dependency Graph·위반 결과·Evidence Manifest
  - 개발자·CI 사용법과 허용 예외 변경 통제 문서
- 제외:
  - CI Workflow와 Merge 차단 설정(`R1-M1-05`)
  - App·Service Runtime Source, Connector 구현, BFF/API Endpoint 구현
  - 기존 Package Version·Lockfile·Toolchain 변경
  - 외부 배포, Commit·Push·PR
- 변경 허용 경로:
  - `package.json`의 독립성 검사 Script 항목
  - `independence-policy.json`
  - `scripts/lib/independence-check.mjs`
  - `scripts/verify-repository-independence.mjs`
  - `scripts/tests/independence-check.test.mjs`
  - `docs/01_architecture/repository_independence_contract.md`
  - `docs/04_test_reports/release_1/R1-M1-04_progress.md`
  - `docs/02_work_orders/reports/R1-M1-04_attempt-1.md`
  - `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`
  - `docs/03_evidence/release_1/R1-M1-04/violations.json`
  - `docs/03_evidence/release_1/R1-M1-04/manifest.json`
- 변경 금지 경로: 위 허용 경로 이외 전체. `package-lock.json`, Toolchain Pin, App/Service Source, 승인 설계·계획·결정·Baseline Manifest·선행 Evidence·`AGENTS.md`·`.agents/`·`.codex/`는 수정하지 않는다.

다른 작업자의 변경을 되돌리거나 정리하지 않는다. R1-M1-03에서 남은 무시 대상 `node_modules`는 이번 구현 산출물이 아니며 삭제를 반복하지 않는다. 검사·Git 명령은 작업자 경합을 고려해 충분히 기다리고, 단순 60초 경과만으로 실패나 무진행으로 판정하지 않는다.

## 3. 독립성 검사 계약

### 3.1 검사 대상과 자체 제외

- 기본 대상은 저장소의 App·Service·Package·일반 Script·Root Manifest·Docker/Compose·CI 설정이다.
- `node_modules`, `.git`, Build/Cache, 문서·보고·Evidence와 검사기 자체 Policy/구현/Test Fixture는 일반 문자열 Scan에서 제외한다.
- 자체 제외는 금지 문자열을 검사 규칙과 음성 Test에 보관하기 위한 최소 범위다. 다른 실행 코드를 임의 예외 처리하지 않는다.
- Package Manifest·Lockfile·`repo-boundaries.json`은 별도 구조 검사로 항상 포함한다.

### 3.2 필수 위반 유형

1. Dependency Graph: 미등록 구성요소, 자기 의존, 순환, `allowed_dependencies` 밖 의존과 `forbidden_dependencies` 의존.
2. Package: Daon2·Daon2.5·Daon3 내부 Package 또는 저장소 상대/절대 Package 직접 의존.
3. Source Import: App↔App, Service 내부 상호 Import, Client→Service Source Import, 다른 Daon Repository/Module Import.
4. Path: `D:\Project\Daon2`, Daon2.5/Daon3 또는 개인 외부 절대경로를 실행 설정·Source에서 참조.
5. Runtime Image: Dockerfile·Compose·CI에서 다른 Daon 제품 Image/Base Image를 사용.
6. Browser URL: Browser 실행 후보에서 `http://`, `https://`, `localhost`, `127.0.0.1`, Docker 내부 Host/Port, `NEXT_PUBLIC_API_BASE_URL`로 API를 직접 호출.
7. Connector 우회: `services/api`의 승인된 Daon Connector Adapter 경계 밖에서 Daon 내부 Client/SDK/Endpoint Module을 Import·호출.

표시용 사용자 문구인 “Daon 승인 지식” 같은 일반 문자열은 위반이 아니다. 표준 Connector 경계의 이름·공개 Contract 참조도 허용하되 내부 URL·SDK·DB·Source 직접 의존은 허용하지 않는다.

### 3.3 Browser/Server 구분

- `apps/web`의 Client Component, Browser Utility와 공유 UI에서 실행될 수 있는 파일을 Browser 후보로 분류한다.
- Next Route Handler, Server Action, 명시적 `.server.*`와 BFF/Proxy 전용 파일은 Server 후보로 분리한다.
- Browser 후보의 API 호출은 same-origin 상대 경로만 허용한다. Native Client는 이 검사에서 Browser로 오분류하지 않으며 후속 공개 HTTPS Gateway 계약으로 검사한다.
- 정적 검사는 실행 증거를 대체하지 않는다. 후속 화면 Work Order는 실제 Browser Network에서 same-origin을 다시 검증한다.

### 3.4 결과 계약

- CLI 성공: Exit 0, 위반 0건, Component/Edge/검사 파일 수를 출력하고 JSON Graph·위반 결과를 생성한다.
- CLI 실패: Exit 1, `rule_id`, 파일, 줄, 안전하게 Masking된 근거와 수정 경계를 반환한다.
- Policy Schema 오류나 검사 불능: Exit 2로 구분한다.
- 예외는 `rule_id`, 정확 경로, 사유, 소유자, 만료/재검토 조건이 있어야 하며 이번 Work Order에서는 제품 예외를 추가하지 않는다.

## 4. 단계와 복구 기록

| 단계 | 작업 | 단계 완료조건 |
| --- | --- | --- |
| S0 | 정본 EOF·Hash·선행 Evidence·Branch·Dirty·단일 Writer 확인 | 시작 Snapshot과 적용 조항 기록 |
| S1 | 현재 경계·Manifest·실행 파일 유형과 회귀 위험 분석 | Policy 항목·검사 대상·자체 제외 확정 |
| S2 | Policy·검사 Library·CLI·문서 작성 | 7개 위반 유형과 Exit 계약 구현 |
| S3 | 위반 유형별 Node Test 작성·실행 | 각 음성 Fixture 실패, 정상 Fixture 통과 |
| S4 | 실제 Repository 검사와 Graph·위반 JSON 생성 | 위반 0, Graph/결과 Parse 성공 |
| S5 | Diff·무관 변경·완료조건 최종 대조, Evidence·결과보고 작성 | 허용 범위 밖 Diff 0, 종료 Snapshot |

`docs/02_work_orders/templates/progress_template.md`를 사용해 지정 진행 파일을 착수, 각 단계 완료, 오류·복구, 각 테스트, 결과보고 제출과 종료 직전에 즉시 갱신한다. 필수 필드는 `recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`다.

## 5. 테스트와 완료조건

### 필수 자동·정적 검증

- 승인 설계·계획·Manifest·작업지시서 SHA-256 일치
- `git branch --show-current`가 `codex/r1-m1-04`
- `git merge-base --is-ancestor 02cce4bb46eaa7ea36fab7c131cd9c328df8114d HEAD`
- `node --test scripts/tests/independence-check.test.mjs` 성공
- `node scripts/verify-repository-independence.mjs` 성공과 위반 0건
- `npm run verify:independence` 성공
- 정상 Fixture와 7개 위반 유형의 음성 Fixture가 정확한 `rule_id`·Exit 분류로 판정
- Dependency Graph JSON의 8개 구성요소·등록 Edge·순환 0건 일치
- `package-lock.json`과 R1-M1-03 Evidence Hash 불변
- `git diff --check`, 추적 파일 삭제 0건, 허용 경로 밖 변경 0건

### 완료조건

- Daon 내부 DB·URL·Path·Import·Image·Package 직접 의존과 Connector 우회가 실제 저장소에서 0건이다.
- 위반을 삽입한 Test가 검사 실패를 증명해 “0건”이 빈 검사 결과가 아님을 보인다.
- Browser/Server 후보 분류와 same-origin 정적 제한이 문서·Policy·Test에서 일치한다.
- 검사기 자체 제외가 최소 정확 경로로 제한되고 일반 Source 예외는 0건이다.
- 기존 Toolchain·Lockfile·App/Service 경계를 변경하지 않았다.
- 수행 명령·Exit Code·Hash·Graph·한계가 Evidence Manifest에 기록된다.

## 6. 결과보고 계약

결과는 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나로 제출한다. `docs/02_work_orders/templates/work_report_template.md`를 사용해 다음 필드를 빠짐없이 포함한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

첫 오류만으로 실패보고하지 말고 원인·대안·현재 Diff·테스트를 조사한다. 승인 범위 변경이 필요하면 구현하지 말고 증거와 선택지를 보고한다.

- 중대한 미진: 필수 위반 유형 누락, 실제 위반 미탐지, 정상 Source 오탐으로 실행 불가, 검사 우회 가능, Lockfile/승인 경계 변경, 필수 증거 부재
- 경미 보완: 완료조건을 깨지 않는 문구·표시·증거 정리
- 사소한 보완만으로 합격 작업 전체를 다시 열지 않는다.

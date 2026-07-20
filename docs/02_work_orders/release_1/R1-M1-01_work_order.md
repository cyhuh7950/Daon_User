# 작업지시서 `R1-M1-01`

## 1. 문서 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M1-01` |
| 버전 / SHA-256 | `1.0` / 작업지시 프롬프트와 Attempt Ledger에 고정 |
| issue_id | `R1-M1-01-I001` |
| 상태 / 시도 | `READY` / `1` |
| 단일 Writer | 어울2 · `daon-developer` |
| 선행조건 | `G0-BASELINE` · `APR-G0-BASELINE-20260720-01` |
| 기준 Commit | `dbb9aa2ff5c40dec9c9a711cc39643580c67f08f` |
| 상세 설계 정본 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` · v0.6 · `7FC4BCE7B517E915520F587D812A241E59F6C8B492671B6C8A4BC53140393C31` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` · v0.7 · `1C628D741D69FD1A977B3A751C36D284A156778428DA0855353EACFAEE6EE82F` |
| 승인 기준 Manifest | `docs/02_work_orders/release_1_baseline_manifest.json` · `CBA9B3DF83883FEF34301B46BF8C3E9E13A3432B0DA0CA0F11D660E60493B4B8` |
| 진행 복구 기록 | `docs/02_work_orders/progress/R1-M1-01.md` |
| 결과보고서 | `docs/02_work_orders/reports/R1-M1-01_attempt-1.md` |

작업자는 `AGENTS.md`, 승인 문서와 이 작업지시서를 EOF까지 읽고 Hash를 확인한 뒤 시작한다. 요약본은 정본을 대체하지 않는다. 실제 저장소 상태가 계획과 다르면 증거를 남기고 승인 경계를 넘지 않은 상태에서 어울1에게 보고한다.

## 2. 목표와 범위

- 단일 목표: 승인된 G0 기준 Commit을 승계하는 독립 개발 Branch와 Git 운영·보호 기준을 수립하고 기존 파일이 보존됐음을 증명한다.
- 사용자 관점 완료 조건: 이후 개발 작업이 승인 기준선에서 추적 가능하게 시작되고, 기존 문서·파일이 손실되거나 무관하게 변경되지 않는다.
- 포함:
  - 기준 Commit과 원격 `origin` 확인
  - 기준 Commit에서 로컬 개발 Branch `codex/release-1` 생성 또는 정확한 승계 상태 확인
  - 기준선·개발 Branch·Work Order Branch·Commit·PR·보호 규칙을 `docs/01_architecture/git_development_baseline.md`에 기록
  - 원격 보호 설정의 실제 적용 여부를 읽기 전용으로 확인하고, 확인 불가 또는 미적용이면 문서에 `NOT_VERIFIED`로 명시
  - 기존 추적 파일의 보존과 허용 범위 밖 Diff 0건 확인
- 제외:
  - Monorepo·애플리케이션·패키지·CI 구현(`R1-M1-02` 이후 범위)
  - GitHub 원격 Branch protection 설정 변경
  - 코드·설정·의존성·기존 설계 계약 변경
  - Commit·Push·외부 배포(검토 후 어울1 수행)
- 변경 허용 경로:
  - Git 로컬 Branch 참조(`codex/release-1` 생성에 한함)
  - `docs/01_architecture/git_development_baseline.md`
  - `docs/02_work_orders/progress/R1-M1-01.md`
  - `docs/02_work_orders/reports/R1-M1-01_attempt-1.md`
  - `docs/03_evidence/release_1/R1-M1-01/manifest.json`
- 변경 금지 경로: 위 허용 경로 이외 전체. 특히 승인 설계서, 작업계획서, Baseline Manifest, `AGENTS.md`, `.agents/`, `.codex/`, 앱·패키지 코드는 수정하지 않는다.

요구되지 않은 리팩터링·구조 변경·전체 재작성·설정값 임의 변경·임시 운영 구조를 금지한다. 다른 작업자가 만든 변경을 되돌리거나 정리하지 않는다.

## 3. Git 기준선 계약

1. `master`는 승인·Release 기준 Branch로 유지한다.
2. Release 1 통합 개발 Branch는 `codex/release-1`이며 `dbb9aa2ff5c40dec9c9a711cc39643580c67f08f`를 조상으로 가져야 한다.
3. 후속 Work Order Branch는 원칙적으로 `codex/<work-order-id-lowercase>` 형식으로 `codex/release-1`에서 분기한다. 단일 Writer와 한 Work Order 한 변경 범위를 유지한다.
4. Commit은 Work Order 단위의 검토 가능한 크기로 만들고 메시지에 작업 목적을 표시한다. Commit·Push는 어울1의 검증 후 수행한다.
5. `master`와 `codex/release-1`의 목표 보호 규칙은 직접 Push 금지, PR 기반 병합, 필수 CI 통과, 미해결 검토 의견 0건, 강제 Push·삭제 금지다. 이번 작업은 정책 문서화와 실제 상태의 읽기 전용 확인까지만 수행한다.
6. 원격 보호가 미설정이거나 조회 권한이 없더라도 임의로 변경하지 않는다. `NOT_VERIFIED` 또는 `EXTERNAL_BLOCKED`와 필요한 후속 권한을 보고한다.
7. 승인된 문서 기준 Commit `c94e553f3a6aa7d062645391e838e7a555706914`, G0 승인 Commit `3397b57882d0e9580bc2561403d07bee65396d92`, 현재 패킷 기준 Commit의 연결을 문서와 증거 Manifest에 기록한다.

Browser/API/화면 구현은 이번 범위에 없다. 따라서 same-origin 및 화면 표준은 변경 대상이 아니며, 후속 작업에서 그대로 준수한다.

## 4. 단계와 복구 기록

| 단계 | 작업 | 단계 완료조건 |
| --- | --- | --- |
| S0 | 정본 EOF 읽기, Hash·기준 Commit·현재 Branch·Dirty 상태·단일 Writer 확인 | 시작 Snapshot과 적용 조항 기록 |
| S1 | Git 원격·Commit 계보·기존 추적 파일·회귀 위험 확인 | 사전 명령과 영향 분석 기록 |
| S2 | `codex/release-1` 생성/확인 및 Git 기준선 문서 작성 | Branch 조상 관계와 정책 문서 존재 |
| S3 | Git 구조·문서 계약 자동/정적 검증 | 모든 필수 명령 Exit 0 또는 제한 사유 명시 |
| S4 | 원격 Branch·보호 상태 읽기 전용 확인 | 확인 결과를 `VERIFIED`/`NOT_VERIFIED`로 구분 |
| S5 | Diff·무관 변경·완료조건 최종 대조, Evidence Manifest·결과보고 작성 | 허용 범위 밖 Diff 0건, 종료 Snapshot 기록 |

`docs/02_work_orders/templates/progress_template.md`를 사용해 지정된 진행 파일을 만들고 다음 시점마다 즉시 갱신한다.

1. 착수 직후
2. 각 단계 완료 직후
3. 오류 발생 직후와 원인 확인·복구 직후
4. 각 테스트 실행 직후
5. 결과보고 제출과 종료·중단 직전

각 기록은 `recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`를 포함한다. 중단 후에는 마지막 완료 단계의 `next_step`부터 재개할 수 있어야 한다. Secret·Token·개인정보는 기록하지 않는다.

## 5. 테스트와 완료조건

### 필수 자동·정적 검증

- `git status --short --untracked-files=all`
- `git rev-parse --show-toplevel`, `git rev-parse HEAD`, `git branch --show-current`
- `git merge-base --is-ancestor dbb9aa2ff5c40dec9c9a711cc39643580c67f08f HEAD`
- `git merge-base --is-ancestor c94e553f3a6aa7d062645391e838e7a555706914 HEAD`
- `git remote -v`, `git ls-remote --heads origin`
- 승인 설계·계획·Manifest·작업지시서 SHA-256 재계산
- `git diff --check`
- 허용 경로 밖 변경 0건 확인

### 필수 실제 검증

- 로컬 Branch 이름이 `codex/release-1`이고 두 기준 Commit을 조상으로 갖는지 실제 Git 명령으로 확인한다.
- 원격 Branch와 보호 상태는 가능한 읽기 전용 수단으로 확인하고, 확인 수준을 Git 원격 조회와 GitHub 보호 규칙 조회로 구분한다.
- 애플리케이션 Process·화면·Network 검증은 기능 코드가 없는 이번 Work Order에는 `NOT_APPLICABLE`로 근거를 남긴다.

### 완료조건

- `codex/release-1`이 정확한 기준 Commit에서 이어진다.
- Git 운영·보호 기준 문서가 이후 Work Order의 Branch·Commit·PR 규칙을 명확히 제공한다.
- 기존 추적 파일 삭제 0건, 허용 범위 밖 Diff 0건이다.
- 수행 명령, Exit Code, 제한 사항과 SHA-256이 `docs/03_evidence/release_1/R1-M1-01/manifest.json`에 기록된다.
- 기본 테스트와 확인을 완료하고 결과보고서가 결과 계약을 충족한다.

## 6. 결과보고 계약

결과는 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나로 제출한다. `docs/02_work_orders/templates/work_report_template.md`를 사용하고 다음 필드를 빠짐없이 포함한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

첫 오류만으로 실패보고하지 말고 원인·가능한 대안·현재 Diff·테스트를 조사한다. 승인 범위 변경이 필요하면 구현하지 말고 증거와 선택지를 어울1에게 보고한다.

- 중대한 미진: Branch 계보 오류, 기존 파일 손실, 허용 범위 밖 변경, 기준 Hash 불일치, 필수 증거 부재
- 경미 보완: 핵심 완료조건을 깨지 않는 문구·증거 정리
- 사소한 보완만으로 합격 작업 전체를 다시 열지 않는다.

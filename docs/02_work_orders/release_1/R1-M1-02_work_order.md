# 작업지시서 `R1-M1-02`

## 1. 문서 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M1-02` |
| 버전 / SHA-256 | `1.0` / 작업지시 프롬프트와 Attempt Ledger에 고정 |
| issue_id | `R1-M1-02-I001` |
| 상태 / 시도 | `READY` / `1` |
| 단일 Writer | 어울2 · `daon-developer` |
| 선행조건 | `R1-M1-01 COMPLETED` |
| 선행 Evidence | `docs/03_evidence/release_1/R1-M1-01/manifest.json` · `07C8183EA4686F92B4EE03F1A2FD1C770112B2B5870C652799244AD50D3E5780` |
| 기준 Branch / Commit | `codex/r1-m1-02` / `ce5974ae10b7bbbdd0042b009b8484c8b631a6c7` |
| 상세 설계 정본 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` · v0.6 · `7FC4BCE7B517E915520F587D812A241E59F6C8B492671B6C8A4BC53140393C31` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` · v0.7 · `1C628D741D69FD1A977B3A751C36D284A156778428DA0855353EACFAEE6EE82F` |
| 승인 기준 Manifest | `docs/02_work_orders/release_1_baseline_manifest.json` · `CBA9B3DF83883FEF34301B46BF8C3E9E13A3432B0DA0CA0F11D660E60493B4B8` |
| 진행 복구 기록 | `docs/02_work_orders/progress/R1-M1-02.md` |
| 결과보고서 | `docs/02_work_orders/reports/R1-M1-02_attempt-1.md` |

작업자는 `AGENTS.md`, 승인 문서와 이 작업지시서를 EOF까지 읽고 Hash를 확인한 뒤 시작한다. 요약본은 정본을 대체하지 않는다. 실제 저장소 상태가 계획과 다르면 증거를 남기고 승인 경계를 넘지 않은 상태에서 어울1에게 보고한다.

## 2. 목표와 범위

- 단일 목표: Web·Windows·Mobile·API·Local Service·공용 UI/Contract/Token의 독립 Monorepo 디렉터리와 소유·의존 경계를 기계 판독 가능한 기준으로 수립한다.
- 사용자 관점 완료 조건: 각 Client와 Service가 후속 Work Order에서 독립적으로 Build될 위치와 책임이 명확하고, 내부 구현을 서로 직접 참조하지 않는 구조다.
- 포함:
  - `apps/web`, `apps/desktop`, `apps/mobile` 경계와 책임 문서
  - `services/api`, `services/local-service` 경계와 책임 문서
  - `packages/ui`, `packages/contracts`, `packages/design-tokens` 공용 패키지 경계와 책임 문서
  - 루트 `repo-boundaries.json`의 구성요소·소유 역할·허용 의존 방향·금지 의존 규칙
  - `docs/01_architecture/monorepo_ownership_boundaries.md`의 사용자 흐름·Runtime·same-origin/BFF·IPC·공개 API·데이터 소유 경계
  - 각 경계 디렉터리의 `README.md`에 책임, 허용 의존, 금지 의존, 후속 Build 소유 Work Order 기록
- 제외:
  - Framework scaffold, 실행 코드, `package.json`, Workspace Manager, Lockfile, Toolchain Pin(`R1-M1-03`)
  - 독립성 검사 Script·CI(`R1-M1-04`, `R1-M1-05`)
  - API Endpoint·OpenAPI·인증·DB·Queue·Connector 구현
  - 의존성 설치, Build, Commit, Push, PR, 외부 배포
- 변경 허용 경로:
  - `apps/web/README.md`
  - `apps/desktop/README.md`
  - `apps/mobile/README.md`
  - `services/api/README.md`
  - `services/local-service/README.md`
  - `packages/ui/README.md`
  - `packages/contracts/README.md`
  - `packages/design-tokens/README.md`
  - `repo-boundaries.json`
  - `docs/01_architecture/monorepo_ownership_boundaries.md`
  - `docs/02_work_orders/progress/R1-M1-02.md`
  - `docs/02_work_orders/reports/R1-M1-02_attempt-1.md`
  - `docs/03_evidence/release_1/R1-M1-02/manifest.json`
- 변경 금지 경로: 위 허용 경로 이외 전체. 승인 설계·계획·Manifest, 기존 R1-M1-01 증거, `AGENTS.md`, `.agents/`, `.codex/`는 수정하지 않는다.

다른 작업자의 변경을 되돌리거나 정리하지 않는다. 빈 Placeholder 디렉터리를 양산하지 말고 위 경계마다 책임을 설명하는 `README.md`로 추적 가능하게 만든다.

## 3. Monorepo 소유·의존 계약

### 3.1 Runtime 소유

- `apps/web`: Browser UI와 Server-side BFF 경계. Browser Fetch는 same-origin 상대 경로만 사용한다.
- `apps/desktop`: Tauri 2 Shell, Web 공용 React UI 호스팅, 서명된 Local Service 수명주기·IPC/Loopback 연결 경계.
- `apps/mobile`: iOS·Android React Native Client. 공개 Gateway 계약만 사용하며 서버·Local Service 내부 구현을 Import하지 않는다.
- `services/api`: FastAPI 기반 공개 API·AI Orchestrator·Cloud-side Adapter 경계. Client별 UI 코드를 소유하지 않는다.
- `services/local-service`: Windows Local-private Runtime·Local 저장·Managed Local Model Adapter 경계. 외부 Listen을 기본으로 열지 않는다.

### 3.2 공용 패키지 소유

- `packages/ui`: Web·Desktop이 공유하는 표현 Component 경계. API 주소·Secret·서버 구현을 소유하지 않는다. Mobile Native UI는 직접 의존하지 않고 Token/Contract만 공유한다.
- `packages/contracts`: 공개 요청·응답·이벤트·오류·Snapshot의 언어 중립 Schema 원천 경계. Provider SDK·Runtime 구현·Secret을 포함하지 않는다.
- `packages/design-tokens`: 색·간격·Typography·반응형·상태 Token의 플랫폼 중립 원천 경계.

### 3.3 의존 방향

- Client/App는 허용된 공용 패키지만 소스 의존할 수 있다.
- App 간, Service 간 내부 소스 Import를 금지한다. Service 연동은 공개 Contract와 Runtime API/IPC를 사용한다.
- `packages/ui`는 `packages/contracts`, `packages/design-tokens`만 의존할 수 있다.
- `packages/contracts`와 `packages/design-tokens`는 다른 저장소 구성요소에 의존하지 않는 Leaf 원천이다.
- `services/api`와 `services/local-service`는 `packages/contracts`만 공유할 수 있고 서로의 내부 소스를 Import하지 않는다.
- Daon은 `services/api`의 표준 Connector Adapter 바깥에서 직접 의존하지 않으며 이번 작업에서는 구현하지 않는다.

`repo-boundaries.json`에는 최소 `schema_version`, `components`, 각 구성요소의 `path`, `kind`, `owner`, `runtime`, `allowed_dependencies`, `forbidden_dependencies`, `build_owner_work_order`를 포함한다. 모든 의존 대상은 등록된 구성요소여야 하고 자기 의존·순환 의존은 없어야 한다.

## 4. 단계와 복구 기록

| 단계 | 작업 | 단계 완료조건 |
| --- | --- | --- |
| S0 | 정본 EOF·Hash·선행 Evidence·Branch·Dirty·단일 Writer 확인 | 시작 Snapshot과 적용 조항 기록 |
| S1 | 사용자 흐름·Runtime·데이터·API·same-origin 영향과 회귀 위험 분석 | 경계 결정과 금지 의존 목록 기록 |
| S2 | 디렉터리·README·기계 판독 경계 Manifest 작성 | 지정된 8개 경계와 루트 Manifest 존재 |
| S3 | 구성요소·경로·의존 대상·자기 의존·순환 의존 정적 검증 | 등록 누락 0, 자기 의존 0, 순환 0 |
| S4 | 각 README와 설계·계획 계약 대조 | Client/API/Local/공용 소유 충돌 0 |
| S5 | Diff·무관 변경·완료조건 최종 대조, Evidence·결과보고 작성 | 허용 범위 밖 Diff 0, 종료 Snapshot |

`docs/02_work_orders/templates/progress_template.md`를 사용해 지정 진행 파일을 착수, 각 단계 완료, 오류·복구, 각 테스트, 결과보고 제출과 종료 직전에 즉시 갱신한다. 필수 필드는 `recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`다.

## 5. 테스트와 완료조건

### 필수 자동·정적 검증

- 승인 설계·계획·Manifest·작업지시서 SHA-256 일치
- `git branch --show-current`가 `codex/r1-m1-02`
- `git merge-base --is-ancestor ce5974ae10b7bbbdd0042b009b8484c8b631a6c7 HEAD`
- `repo-boundaries.json` JSON Parse 성공
- 필수 8개 구성요소와 실제 경로·README 존재
- 모든 `allowed_dependencies`·`forbidden_dependencies` 대상이 등록된 구성요소인지 검사
- 자기 의존·순환 의존 0건
- App→App, Service→Service 내부 소스 의존 0건
- `git diff --check`, 추적 파일 삭제 0건, 허용 경로 밖 변경 0건

개발 검증 명령은 사용할 수 있지만 사용자·운영 절차에 Python·DB CLI를 추가하지 않는다. 의존성 설치나 Build는 이번 범위에서 `NOT_APPLICABLE`로 근거를 남긴다.

### 완료조건

- Web·Desktop·Mobile·API·Local Service·UI·Contract·Token의 경계와 소유가 문서·JSON에서 일치한다.
- 각 App의 후속 독립 Build 위치와 담당 Work Order가 명시된다.
- Browser same-origin/BFF, Desktop IPC/Loopback, Mobile 공개 Gateway 경계가 보존된다.
- 순환 의존과 내부 구현 교차 Import를 허용하는 경로가 0건이다.
- 기존 파일 삭제 0건, 허용 범위 밖 Diff 0건이다.
- 수행 명령·Exit Code·SHA-256·제한 사항이 Evidence Manifest에 기록된다.

## 6. 결과보고 계약

결과는 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나로 제출한다. `docs/02_work_orders/templates/work_report_template.md`를 사용해 다음 필드를 빠짐없이 포함한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

첫 오류만으로 실패보고하지 말고 원인·대안·현재 Diff·테스트를 조사한다. 승인 범위 변경이 필요하면 구현하지 말고 증거와 선택지를 보고한다.

- 중대한 미진: 경계 누락, 순환 의존, same-origin/공개 API/데이터 소유 위반, 허용 범위 밖 변경, 필수 증거 부재
- 경미 보완: 완료조건을 깨지 않는 문구·표시·증거 정리
- 사소한 보완만으로 합격 작업 전체를 다시 열지 않는다.

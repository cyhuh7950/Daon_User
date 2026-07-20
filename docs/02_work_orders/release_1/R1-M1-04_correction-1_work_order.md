# 수정 작업지시서 `R1-M1-04-C01`

## 1. 문서 계약

| 항목 | 값 |
| --- | --- |
| 원 Work Order / issue_id | `R1-M1-04` / `R1-M1-04-I001` |
| 수정 차수 / Attempt | `C01` / `2` |
| 상태 | `READY` |
| 단일 Writer | 어울2 · `daon-developer` |
| 원 작업지시서 | `docs/02_work_orders/release_1/R1-M1-04_work_order.md` v1.0 |
| 원 결과보고 | `docs/02_work_orders/reports/R1-M1-04_attempt-1.md` · 개발자 `COMPLETED`, 어울1 `INCOMPLETE` 재분류 |
| 진행 복구 기록 | `docs/04_test_reports/release_1/R1-M1-04_progress.md` |
| 수정 결과보고 | `docs/02_work_orders/reports/R1-M1-04_attempt-2.md` |

작업자는 `AGENTS.md`, 승인 상세 설계서 v0.6, Release 1 계획 v0.8, 승인 기준 Manifest, 선행 Evidence, 원 작업지시서·프롬프트·진행 기록·Attempt 1 결과와 이 수정 작업지시서를 EOF까지 읽는다. 이 문서는 원 작업지시서의 누락을 보완하며 원 범위·제외·승인 경계를 변경하지 않는다.

## 2. 판정과 수정 사유

- 판정: `INCOMPLETE` · 중대 미진. 유효한 `FAILURE_REPORT`는 0회, `INCOMPLETE` 누적 1회다.
- 원 작업지시서 §3.1은 Package Manifest·Lockfile을 일반 문자열 제외와 무관하게 구조 검사하도록 요구한다. 현재 구현은 구성요소별 `package.json` 또는 `pyproject.toml`만 읽어 루트 `package.json`과 `package-lock.json`의 금지 Package 의존을 검사하지 않는다.
- 원 작업지시서 §3.2는 Source Import 직접 의존을 차단하도록 요구한다. 현재 Import 추출은 인용부호가 있는 JavaScript 계열만 처리하여 Python `import`·`from ... import ...`와 JavaScript/TypeScript `export ... from` 재수출을 놓칠 수 있다.
- 보고·문서가 실제 구현보다 넓은 보장을 주장하므로 사소한 보완이 아니라 검사 계약의 중대한 미진이다.

## 3. 수정 범위

### 3.1 필수 구현

1. 루트 `package.json`, 모든 등록 구성요소의 Package Manifest, 루트 `package-lock.json`을 별도 구조 검사 대상으로 포함한다.
2. 금지 Package 이름, 다른 Daon 제품 Package, `file:`·`link:`·상대/절대 저장소 경로 의존을 루트 Manifest와 Lockfile에서도 `PACKAGE_DAON_INTERNAL`로 차단한다.
3. Lockfile의 정상 Registry URL·Integrity·Workspace Link 메타데이터 자체는 위반으로 오탐하지 않는다. Package identity와 의존 Spec·경로 필드의 의미를 구조적으로 판정한다.
4. Lockfile이 없으면 현재 승인 기준과의 관계를 명시적으로 처리하고, 존재하지만 JSON을 읽지 못하면 Exit 2 검사 불능으로 판정한다.
5. JavaScript/TypeScript의 `import`, 동적 `import()`, `require()`, Side-effect Import와 `export ... from` 재수출을 검사한다.
6. Python `import module`, `import module as alias`, `from module import name`에서 다른 Daon 제품 Module 직접 의존을 `SOURCE_IMPORT_BOUNDARY`로 차단한다. 주석·문자열 일반 문구는 Import로 오인하지 않는다.
7. 현재 7개 위반 범주, Browser/Server 분류, Connector Adapter 경계와 Exit 0/1/2 계약을 유지한다.

### 3.2 허용 변경 경로

- `independence-policy.json` — 구조 검사 대상 선언 보완이 필요한 경우만
- `scripts/lib/independence-check.mjs`
- `scripts/verify-repository-independence.mjs` — Exit/출력 계약 보완이 필요한 경우만
- `scripts/tests/independence-check.test.mjs`
- `docs/01_architecture/repository_independence_contract.md`
- `docs/04_test_reports/release_1/R1-M1-04_progress.md`
- `docs/02_work_orders/reports/R1-M1-04_attempt-2.md`
- `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`
- `docs/03_evidence/release_1/R1-M1-04/violations.json`
- `docs/03_evidence/release_1/R1-M1-04/manifest.json`

이 밖의 파일은 수정하지 않는다. 특히 `package.json`, `package-lock.json`, App/Service Source, 승인 설계·계획·기준 Manifest, 원 작업지시서·프롬프트, Attempt 1 결과보고, `AGENTS.md`, `.agents/`, `.codex/`는 변경하지 않는다. 기존 작업자의 변경을 되돌리거나 정리하지 않는다.

## 4. 필수 회귀·음성 테스트

- 기존 Node Test 9건이 모두 계속 통과한다.
- 정상 루트 `package.json`과 정상 Lockfile Fixture가 Exit 0이다.
- 루트 `package.json`의 다른 Daon 내부 Package가 `PACKAGE_DAON_INTERNAL`, Exit 1이다.
- Lockfile에 주입한 다른 Daon 내부 Package 또는 금지 로컬 경로 의존이 `PACKAGE_DAON_INTERNAL`, Exit 1이다.
- Python `from daon2.internal import Client` 또는 동등한 직접 Import가 `SOURCE_IMPORT_BOUNDARY`, Exit 1이다.
- JavaScript/TypeScript `export { value } from '../../../services/api/src/internal.js'` 또는 동등한 재수출이 `SOURCE_IMPORT_BOUNDARY`, Exit 1이다.
- Python 주석·일반 문자열과 정상 외부 Package Import가 오탐되지 않는다.
- 실제 저장소 CLI와 `npm run verify:independence`가 Exit 0, Graph 8개·Edge 10개·순환 0·위반 0이다.
- `package-lock.json` Git blob SHA-256가 선행 Evidence 값 `8B8EE4FCE6750FFA03827C972890A4C7E1D4FF334AAB82D085FD0119BA2C689D`와 같고 추적 Diff가 없다.
- `git diff --check`, 추적 삭제 0건, 수정 작업지시 허용 경로 밖 신규 변경 0건을 확인한다.

테스트 수만 보고하지 말고 각 추가 Fixture의 입력, 기대 `rule_id`, 실제 Exit Code를 Evidence Manifest에 남긴다. 기존 Evidence는 재실행 결과로 갱신하고 Attempt 2 결과보고에서 Attempt 1의 잘못된 보장과 수정 결과를 명확히 구분한다.

## 5. 진행 기록과 결과 계약

- 진행 파일의 기존 기록은 보존하고 `REWORK_ATTEMPT_2` 재개 Snapshot을 Append한다.
- 착수, 원인 재확인, 구현, 각 테스트, 실제 저장소 검사, 최종 Diff·Hash, 종료 직전에 시각·단계·상태·변경 파일·명령/Exit·오류/원인/복구·다음 작업을 기록한다.
- 결과는 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE`, `BLOCKED` 중 하나로 제출하며 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`을 포함한다.
- Server/WSL, Commit·Push·PR은 수행하지 않는다. ysna-server 흐름은 R1-M1-05부터 적용한다.

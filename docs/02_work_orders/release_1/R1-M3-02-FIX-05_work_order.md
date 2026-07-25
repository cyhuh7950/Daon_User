# R1-M3-02-FIX-05 수정 작업지시서

- 원 작업: `R1-M3-02`
- 선행 수정: `R1-M3-02-FIX-01`~`FIX-04`
- Issue ID: `R1-M3-02-PREDECESSOR-LOCK-SUCCESSOR`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-25
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정과 원인

R1-M3-02 구현 커밋 `8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa`의 ysna-server 전체 회귀에서 `211/213`이 통과하고 다음 두 검사가 실패했다.

- `R1-M2-06/evidence-manifest.json`이 이전 `package-lock.json` Hash·Byte를 기대한다.
- `R1-M2-07/evidence-manifest.json`이 같은 이전 `package-lock.json` Hash·Byte를 기대한다.

PostCSS 보안 패치로 현재 Lockfile이 정당하게 변경됐으므로 이는 Linux 이식성 문제가 아니라 로컬에서도 재현되는 선행 증거 계보 미정합이다. 새 기능 테스트는 통과했지만 전체 회귀가 실패하므로 중대 미진으로 판정한다. 정식 실패보고는 아니며 유효한 실패 횟수는 `0회`다.

## 2. 설계 판단

- R1-M2-06·07의 역사적 Manifest Hash를 현재 Lockfile 값으로 덮어쓰지 않는다.
- 승인된 후속 변경인 R1-M3-02 PostCSS 보안 패치가 두 선행 `package-lock.json` Artifact를 대체했다는 계보를 명시한다.
- 기존 `SUCCESSOR_SUPERSEDED` 4건에 위 두 건을 정확히 추가하고 Reconciliation 승인 기준을 `90 / DIRECT_MATCH 80 / SUCCESSOR_SUPERSEDED 6 / LEGACY_MANIFEST_DRIFT 4 / UNEXPLAINED_MISMATCH 0`으로 갱신한다.
- 새 두 Special Case는 선행 Work Order, Artifact 경로, 기존 기대 SHA·Byte, 기원 Commit, 후속 Commit `8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa`, 현재 SHA·Byte를 모두 고정한다.
- 이 변경은 증거 계보 정합화이며 기능·공개 API·데이터 계약·보안 경계를 바꾸지 않는다.

## 3. TDD와 구현 범위

1. 현재 두 실패를 재현하고 RED 근거를 Progress에 기록한다.
2. 두 Lockfile Artifact만 승인된 후속 대체로 분류되는 행동 테스트를 먼저 추가한다.
3. 경로·이전 Hash·Byte·기원 Commit·후속 Commit·현재 Hash·Byte 중 하나라도 다르면 `UNEXPLAINED_MISMATCH`로 Fail-close하는 부정 테스트를 포함한다.
4. 최소 구현으로 Reconciliation Special Case와 승인 집계 계약을 갱신한다.
5. R1-M2-06·07의 역사적 Manifest 원문은 수정하지 않는다.
6. R1-M2-08 Reconciliation 결과·Summary와 R1-M3-02 Source/Evidence Manifest를 최신 상태로 재생성한다.
7. FIX-05 작업지시서·프롬프트와 최신 결과보고·진행 기록을 R1-M3-02 증거 입력에 포함한다.

## 4. 검증과 증거

- 전용 RED→GREEN
- `node --test --test-concurrency=1 scripts/tests/*.test.mjs`에서 `213/213` 이상 전체 통과
- `npm run lint:workspace`
- `npm run build --workspace @daon-user/web`
- `npm run build --workspace @daon-user/desktop`
- 수동 환경변수 없는 `npm run verify:desktop-type`
- `npm audit --omit=dev --audit-level=high --json`
- 수동 환경변수 없는 `npm run verify:quality-gate`
- JSON Parse 및 모든 R1-M3-02 Manifest Source/Evidence Hash·Byte 불일치 `0`
- `git diff --check`
- R1-M1-05 Evidence Dirty `0`
- `gen`, Root/Desktop Cargo Target, Temp Check Target, Daon App Process `0`

각 단계 착수·완료·오류·복구·테스트·종료 직전에 시각, 상태, 변경 파일, 명령과 결과, 오류 원인과 복구, 다음 작업을 Progress에 기록한다.

## 5. 제외·보호 범위

- 제품 기능 코드, 화면, 공개 API, 데이터 계약, 보안 정책을 변경하지 않는다.
- PostCSS `8.5.23`, Next·Vite 버전, Lockfile 현재 내용과 same-origin 경계를 변경하지 않는다.
- R1-M2-06·07 역사적 Manifest를 수정하지 않는다.
- 화면/App 실행, Commit, Push, PR, 서버 배포를 수행하지 않는다.
- 관련 없는 파일과 사용자 변경을 수정하지 않는다.

## 6. 종료 조건

- `docs/02_work_orders/reports/R1-M3-02_attempt-2.md`와 Progress를 최신 수치로 갱신한다.
- 종료 보고는 `status | issue_id | 수행한 작업 | 생성·변경 결과 | 테스트 결과 | 미해결 사항 | 다음 판단` 형식을 사용한다.


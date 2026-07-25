# R1-M3-02-FIX-04 수정 작업지시서

- 원 작업: `R1-M3-02`
- 선행 수정: `R1-M3-02-FIX-01`~`FIX-03`
- Issue ID: `R1-M3-02-POSTCSS-PROBE-EVIDENCE`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-25
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정

FIX-03의 기존 `gen` 보존 경계는 대부분 충족했다. 최종 독립 검토와 어울1 최신 Quality Gate에서 다음 Important가 남았다.

1. `gen` 상태 확인 불가(EACCES/EIO) 분기의 실제 행동 테스트가 없다.
2. Progress의 최종 기록과 Attempt-2 완료 수치가 불일치한다.
3. 2026-07-23 공개된 `GHSA-6g55-p6wh-862q`로 기존 Web의 `next@16.3.0-canary.93 → postcss@8.5.10`이 Production Audit에 실패한다.

신산님은 2026-07-25 계속 진행을 승인했다. 최초 승인 버전 `8.5.12`는 이후 공개·검토된 `GHSA-r28c-9q8g-f849`의 취약 범위 `<=8.5.17`에 포함되므로 폐기한다. 신산님은 같은 날 계속 진행 지시로 PostCSS `8.5.23` 적용을 승인했다. 현재 Workspace에서 PostCSS를 사용하는 Next·Vite 경로를 Root npm Override로 `8.5.23`에 통일하되, Next·Vite 자체 버전과 다른 의존성은 변경하지 않는다.

이는 정식 실패보고가 아니며 실패 횟수는 `0회`다.

## 2. TDD A — 상태 확인 불가 Fail-close

- Filesystem probe를 테스트에서 주입할 수 있는 최소 경계를 제공한다.
- 실행 전 정확한 `apps/desktop/src-tauri/gen`의 상태 확인이 EACCES 또는 EIO로 실패하면:
  - Exit `2`
  - Cargo/Installer Child 호출 `0`
  - Temp Target 생성 `0`
  - 기존 경로·인접 파일·다른 Worktree 변경 `0`
- 먼저 EACCES/EIO 행동 Fixture를 추가해 RED를 확인한 뒤 최소 구현으로 GREEN을 만든다.
- 문자열/정규식 존재 확인만으로 대체하지 않는다.
- 기존 Exit 0·23·Spawn 오류·Sentinel·인접·다른 Worktree 테스트를 유지한다.

## 3. TDD B — PostCSS 보안 패치

- Root `package.json`의 npm `overrides`를 사용해 Workspace의 `postcss`를 정확히 `8.5.23`으로 고정한다. 이는 npm 11이 Next의 정확 버전 의존성 `postcss@8.5.10`에 대한 중첩 Override를 `npm ls`에서 `invalid`로 판정하는 문제를 피하면서, 현재 Next·Vite 경로를 동일한 안전 패치 버전으로 통일하기 위한 승인된 최소 변경이다.
- `npm install --package-lock-only` 또는 동등한 Lockfile 갱신으로 재현 가능한 `package-lock.json`을 만든다.
- 전용 테스트는 다음을 검증한다.
  - Root Override가 `postcss = 8.5.23` 단일 항목으로 제한됨
  - Lockfile의 Next·Vite 경로가 모두 `postcss = 8.5.23`을 사용함
  - Next·Vite 자체 버전과 PostCSS 이외의 의존성은 변경하지 않음
  - `npm ls next vite postcss --all`을 실행해 패치된 두 경로를 확인한다. npm `11.12.1`이 Next manifest의 정확 선언 `postcss=8.5.10`과 Root 보안 Override `8.5.23`의 차이만 `invalid`로 보고하면 Exit `1`을 허용한다. 단, 문제 목록이 이 단일 사유와 정확히 일치하고 `missing`·`extraneous`·다른 `invalid`가 `0`이어야 한다.
  - Lockfile과 실제 `node_modules/postcss/package.json`이 모두 `8.5.23`이고, Next·Vite가 실제 동일 Package를 해석하며 비PostCSS Package Graph Hash가 변경 전과 동일함
  - `npm audit --omit=dev --audit-level=high --json`의 High/Critical이 `0`
- `npm audit fix --force`, Next·Vite 버전 변경, 시스템 전역 npm 설정 변경, Gate 완화와 Advisory 예외 처리를 금지한다.
- `GHSA-6g55-p6wh-862q`와 `GHSA-r28c-9q8g-f849`가 모두 해소됐음을 Audit 증거에 기록한다.
- Lockfile 설치 후 Web·Desktop Build와 회귀 테스트를 모두 재실행한다.

## 4. 문서·증거 정합화

- Progress에 FIX-04 RED·GREEN, Dependency 변경, 최신 Gate와 최종 잔존 검증을 단계별 기록한다.
- Attempt-2의 최상단 계약과 본문을 FIX-04 Issue와 최신 테스트 수치로 갱신한다.
- Generator의 Source/Validation 입력에 FIX-04 작업지시서·프롬프트를 포함한다.
- 최종 Source/Evidence Manifest의 최상위 `issue_id`는 최신 결과보고와 동일한 `R1-M3-02-POSTCSS-PROBE-EVIDENCE`여야 한다. FIX-01~FIX-03 및 GUI 재현 개별 Evidence의 고유 Issue ID는 변경하지 않는다.
- R1-M3-02에 최신 PASS Quality Gate Result/Summary를 보존한 뒤 R1-M1-05 두 파일만 Git 기준선으로 원복한다.
- Source/Evidence Manifest의 모든 Hash·Byte를 재생성하고 불일치 `0`을 확인한다.
- Console `not_observable_in_release_build`, Installer·설치본 유지, GUI L4 증거는 변경하지 않는다.

## 5. 최종 검증

- 전용 RED→GREEN
- `node --test --test-concurrency=1 scripts/tests/*.test.mjs`
- `npm run lint:workspace`
- `npm run build --workspace @daon-user/web`
- `npm run build --workspace @daon-user/desktop`
- 수동 환경변수 없는 `npm run verify:desktop-type`
- `npm audit --omit=dev --audit-level=high --json`
- 수동 환경변수 없는 `npm run verify:quality-gate`
- `npm ls next vite postcss --all` 및 JSON 문제 목록의 허용된 단일 `invalid` 사유 대조
- JSON Parse, Manifest Source/Evidence Hash·Byte 불일치 `0`
- `git diff --check`
- R1-M1-05 Evidence Dirty `0`
- `gen`, Root/Desktop Cargo Target, Temp Check Target, Daon App Process `0`

## 6. 종료 조건

- 기존 기능·same-origin·보안 경계를 유지한다.
- 작업현황을 각 단계와 종료 직전에 기록한다.
- `docs/02_work_orders/reports/R1-M3-02_attempt-2.md`를 갱신한다.
- Commit·Push·PR·배포와 화면/App 실행을 금지한다.
- 종료 보고는 `status | issue_id | 수행한 작업 | 생성·변경 결과 | 테스트 결과 | 미해결 사항 | 다음 판단` 형식을 사용한다.

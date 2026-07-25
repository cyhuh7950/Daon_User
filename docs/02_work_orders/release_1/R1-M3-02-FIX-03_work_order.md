# R1-M3-02-FIX-03 수정 작업지시서

- 원 작업: `R1-M3-02`
- 선행 수정: `R1-M3-02-FIX-01`, `R1-M3-02-FIX-02`
- Issue ID: `R1-M3-02-WRAPPER-GEN-PRESERVATION`
- 판정: `REWORK(C1)`
- 작성: 어울1
- 작성일: 2026-07-23
- 작업 위치: `C:\tmp\Daon_User-r1-m3-02`
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M3-02_progress.md`

## 1. 판정

FIX-02의 Cargo Target 격리와 GUI L4 증거는 합격했다. 다만 최종 독립 코드리뷰에서 `scripts/run-isolated-desktop-cargo.mjs`가 실행 전 존재하던 내용까지 포함해 `apps/desktop/src-tauri/gen` 전체를 재귀 삭제할 수 있다는 Important 1건이 확인됐다.

이는 기능 범위 변경이나 정식 실패보고가 아니라 Merge 전 C1 보존 안전조건 미충족이다. 정식 실패 횟수는 `0회`로 유지한다.

## 2. 수정 계약

- 기존 `apps/desktop/src-tauri/gen` 또는 인접 파일을 임의로 삭제하지 않는다.
- Wrapper가 실행 전에 `gen` 상태를 확인하고, 기존 내용이 있으면 보존을 우선한다.
- 가장 안전한 기본 동작은 실행 전 `gen` 존재를 감지하면 Cargo Child를 시작하지 않고 안정 오류와 Exit `2`로 Fail-close하는 것이다.
- 실행 전 `gen`이 없었던 경우에만 Wrapper가 이번 Cargo 실행으로 생성된 정확한 `gen`을 정리할 수 있다.
- Cargo Child 성공, Exit Code 실패, Spawn 오류에서 동일 보존 규칙을 적용한다.
- Temp Cargo Target 정리와 `gen` 정리는 별도 계약으로 유지한다.
- 저장소 Root, 사용자 Temp Root, 다른 Worktree, 다른 앱 디렉터리 또는 미확인 경로를 재귀 삭제하지 않는다.
- 기존 외부 동작, 명령 이름, 수동 `CARGO_TARGET_DIR` 불필요 계약, Exit Code 전파와 Installer 보존 계약을 유지한다.

## 3. TDD 필수 시나리오

구현 전에 다음 행동 테스트를 RED로 확인하고 GREEN으로 만든다.

1. 실행 전 `gen`이 없고 Cargo가 Schema를 생성한 뒤 Exit `0`이면 생성 `gen`만 제거된다.
2. 실행 전 `gen`이 없고 Cargo가 Schema를 생성한 뒤 Exit `23`이면 Exit `23`을 보존하고 생성 `gen`만 제거된다.
3. 실행 전 `gen`이 없고 Spawn 오류가 발생하면 안정 Exit `2`를 반환하고 생성 잔존물이 없다.
4. 실행 전 `gen`에 Sentinel 또는 Sibling 파일이 있으면 Cargo Child를 실행하지 않고 Exit `2`로 Fail-close하며 기존 Byte·Hash를 그대로 보존한다.
5. Temp Cargo Target 외의 경로와 다른 Worktree의 동명 경로는 변경하지 않는다.

문자열 정규식만으로 `finally` 존재를 확인하는 테스트로 대체하지 말고 실제 임시 Fixture와 파일 보존 결과를 검증한다.

## 4. 증거·보고 정합화

- Wrapper와 전용 테스트 변경을 `source-artifact-manifest.json`에 반영한다.
- Evidence Manifest의 Source Manifest Hash·Byte를 갱신한다.
- FIX-03 RED·GREEN, 전체 검증 결과와 최종 잔존물 0건을 진행 기록에 남긴다.
- `R1-M3-02_attempt-2.md`에 FIX-03 보존 안전성 판정과 검증 결과를 추가한다.
- Console의 `not_observable_in_release_build`와 기존 GUI·Installer 증거는 변경하지 않는다.

## 5. 최종 검증

- 전용 행동 테스트 RED→GREEN
- `node --test --test-concurrency=1 scripts/tests/*.test.mjs`
- `npm run lint:workspace`
- `npm run build --workspace @daon-user/desktop`
- 수동 환경변수 없는 `npm run verify:desktop-type`
- 수동 환경변수 없는 `npm run verify:quality-gate`
- `git diff --check`
- Manifest Source/Evidence Hash·Byte 불일치 0
- R1-M1-05 생성 Evidence Dirty 0
- `apps/desktop/src-tauri/gen`, 저장소 Cargo Target, Daon App Process 잔존 0

Quality Gate가 R1-M1-05 Evidence를 갱신하면 R1-M3-02 최신 Gate 증거를 보존한 뒤 해당 두 파일만 Git 기준선으로 원복한다.

## 6. 종료 조건

- 진행 복구 기록을 단계마다 갱신한다.
- 정식 결과보고 `docs/02_work_orders/reports/R1-M3-02_attempt-2.md`를 갱신한다.
- 보고 형식은 `판정 → 판단 이유 → 조치` 순서다.
- Commit·Push·PR·배포와 설치본 재실행을 금지한다.

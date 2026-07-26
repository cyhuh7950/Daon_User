# R1-M3-04-C02 수정 작업지시서 — Mobile Workspace 표준 명령 복구

## 1. 판정

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M3-04` · issue `R1-M3-04-I001` |
| 검토 결함 | `R1-M3-04-C02-WORKSPACE-SCRIPT` |
| 판정 | 중대 미진 · 별도 수정 작업지시 |
| 발견 주체 | 어울1 완료보고·Diff 검토 |
| 실패보고 누적 | `0` · Attempt 1은 BLOCKED, Attempt 2는 COMPLETED 후 어울1 검토 미통과 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-04_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-04_attempt-3.md` |

판단 이유: Root 전용 `verify:mobile-*`은 통과하지만 `apps/mobile/package.json`의 표준 `lint`, `type`, `unit`, `contract`, `build`가 존재하지 않는 `@daon-user/root` Workspace를 호출한다. 실제 `npm run lint --workspace @daon-user/mobile`, `type`, `contract`가 `No workspaces found: --workspace=@daon-user/root`로 Exit 1이다. R1-M3-05·06이 승계할 Package 명령이 작동하지 않으므로 완료로 인정할 수 없다.

## 2. 수정 계약

- Root의 직접 검증 Script는 구현 명령의 단일 소유를 유지한다.
- `apps/mobile`의 표준 Script는 Workspace 실행 위치에서 저장소 Root를 명시적으로 가리켜 해당 Root 검증 Script를 호출한다.
- Windows와 Linux/macOS에서 동일하게 동작하는 npm `--prefix` 또는 동등한 Shell 비종속 방식을 사용한다. PowerShell·cmd 전용 문법과 절대경로를 넣지 않는다.
- `--workspace @daon-user/root`를 사용하지 않는다. Root Package는 npm Workspaces 배열의 하위 Workspace가 아니다.
- `lint`, `type`, `unit`, `contract`, `build` 다섯 명령을 실제 Mobile Workspace 진입점으로 실행해 모두 Exit 0을 확인한다.
- 공통 Quality Gate의 Mobile Capability는 다섯 Workspace 표준 명령을 직접 실행하게 해, 이후 같은 파손이 Root 우회 명령으로 가려지지 않게 한다.
- Root `verify:mobile`은 순환 호출 없이 다섯 직접 검증을 실행해야 한다.

## 3. TDD·변경 범위

먼저 현재 Manifest에서 다음 RED를 고정한다.

1. 다섯 Workspace Script에 `--workspace @daon-user/root`가 없어야 한다.
2. 다섯 Workspace Script가 저장소 Root의 대응 검증 명령으로 연결되어야 한다.
3. `npm run lint|type|unit|contract|build --workspace @daon-user/mobile` 실제 실행이 모두 Exit 0이어야 한다.
4. Quality Gate Policy의 Mobile 5개 Capability가 Workspace 표준 명령을 직접 실행해야 한다.

허용 변경:

- `apps/mobile/package.json`, 필요 시 Root `package.json`, `package-lock.json`
- `quality-gate-policy.json`
- `scripts/tests/mobile-shared-shell.test.mjs`와 Mobile 전용 검증 Script
- Progress, Attempt 3, R1-M3-04 Evidence 5종·Manifest·Architecture의 정확한 명령 표기

금지 변경:

- Mobile Production Source·Contract·Token 의미 변경
- C01 검증기·Desktop Test·Platform Evidence Test 재변경
- Web·Desktop·Local Service·M2 UI/Domain·과거 Evidence 변경
- Test Skip·조건부 PASS·명령 삭제로 해결
- Commit·Push·PR·Merge·서버·GUI·Native Project

## 4. 완료 증거

- 신규 RED→GREEN과 다섯 실제 Workspace 명령 Exit 0
- C01 지정 Test, 전체 Node 회귀, Mobile 전체, `npm ci --ignore-scripts`, Toolchain, Audit, Independence 전부 PASS
- 최종 공통 7범주 Gate가 Mobile Workspace 명령을 실행하고 Overall PASS·Failures 0
- Android/iOS Headless Production Bundle Hash·Byte 재확인
- `git diff --check`, 승인 정본 9개 Hash 불변, 삭제·관련 없는 Diff 0
- Evidence Manifest Source/Evidence Hash·Byte mismatch 0
- Attempt 2는 보존하고 Attempt 3에서 `판정 → 판단 이유 → 조치`와 정식 7필드 결과 제출

정식 결과 형식:

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

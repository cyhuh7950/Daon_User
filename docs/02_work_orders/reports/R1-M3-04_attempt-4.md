COMPLETED | R1-M3-04-C03-CI-HISTORY | GitHub Checkout에 고정 역사 Blob·Ancestor 검증용 전체 이력 계약을 TDD로 추가 | Workflow·계약 Test·R1-M3-04 Evidence 5종·Manifest·Progress 갱신 | RED 10/11→GREEN 37/37, C01 42/42, 전체 251/251, Mobile 5개·Toolchain·Audit·Independence·공통 Gate 전부 PASS | Commit·Push·PR·GitHub Run 재검증은 어울1 후속 | 어울1의 Diff·Evidence 검토 후 Exact-SHA Commit과 GitHub Quality Gate 재검증 판단

# R1-M3-04 Attempt 4 작업보고

## 판정

`COMPLETED` — C03 결함 `R1-M3-04-C03-CI-HISTORY`를 승인된 기본안 `fetch-depth: 0`으로 TDD 보정했고, 원 R1-M3-04·C01·C02 계약과 전체 로컬 검증을 다시 통과했다.

## 판단 이유

- PR #18 Run `30203630301`에서 Mobile Workspace 5개와 나머지 Gate는 통과했으나 `desktop-shell-unit`만 약 0.29초에 실패했다.
- C01 Desktop Test는 고정 Successor Commit `8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa`의 `package.json`·`package-lock.json` Blob을 `git show`로 읽고 Origin→Successor Ancestor 관계를 검증한다.
- 기존 Workflow의 `actions/checkout@v5`에는 이력 깊이 계약이 없어 얕은 PR Checkout에서 고정 Commit 객체가 없을 수 있었다.
- Checkout에 `fetch-depth: 0`을 명시해 고정 역사 Blob과 Ancestor 검증에 필요한 이력을 확보했다. 고정 Commit·Hash·Byte, 현재 Checkout 핵심 Pin, 검사 Fail-close 의미는 변경하지 않았다.
- Action Major, Step 순서, 권한, Toolchain, Tauri 선행 패키지, Fallback, Evidence Upload, Mobile Workspace 명령과 Quality Gate Capability는 그대로다.

## TDD

RED:

- `node --test scripts/tests/product-foundation.test.mjs`
- Exit 1, `10/11 PASS`
- 신규 계약의 실제값은 `checkout.with=undefined`, 기대값은 `{ "fetch-depth": 0 }`이었다.

GREEN:

- `.github/workflows/release-1-quality-gate.yml`의 Checkout 단계에만 `with: { "fetch-depth": 0 }`을 추가했다.
- Workflow·Quality Gate 관련 Test: Exit 0, `37/37 PASS`
- C01 지정 3 Test: Exit 0, `42/42 PASS`
- Workflow JSON Parse: PASS

## 생성·변경 결과

- `.github/workflows/release-1-quality-gate.yml`: 전체 Git 이력 Checkout 계약 추가
- `scripts/tests/product-foundation.test.mjs`: Checkout 이력 깊이와 기존 Action/순서 계약 고정
- `docs/03_evidence/release_1/R1-M3-04/`: Mobile Contract·Build·Security, Quality Gate Result/Summary, Evidence Manifest를 C03 결과에 결속
- `docs/04_test_reports/release_1/R1-M3-04_progress.md`: 착수·RED/GREEN·환경 오류/복구·전체 검증·종료 기록
- 본 Attempt 4 보고서

Mobile·Desktop·Web·Local Service Production Source, C01 검증기, 의존성, `package-lock.json`, Toolchain Pin, Quality Gate Capability는 변경하지 않았다.

## 전체 검증

| 검증 | 결과 |
| --- | --- |
| Workflow·Quality Gate 관련 Test | Exit 0, 37/37 PASS |
| C01 지정 3 Test | Exit 0, 42/42 PASS |
| 전체 Node 회귀 | Exit 0, 251/251 PASS |
| Mobile Workspace `lint` | Exit 0, 10 files |
| Mobile Workspace `type` | Exit 0 |
| Mobile Workspace `unit` | Exit 0, 9/9 PASS |
| Mobile Workspace `contract` | Exit 0, 15/15 PASS |
| Mobile Workspace `build` | Exit 0, Android/iOS Headless Bundle PASS |
| Android Bundle | 921,664 bytes, SHA-256 `07F68AD055B353EE7C15C5B0B09B15EDEA6D47276B16B465EE7FF07C8F4782CD` |
| iOS Bundle | 916,298 bytes, SHA-256 `D8A2CBB174B958AC85288F78B5AA6A2C1DF5FE93AE07DCA7A69387609BD6CB2B` |
| `npm ci --ignore-scripts` | Exit 0, 342 packages |
| Toolchain | Exit 0, 7 npm manifests exact pins |
| Production Audit | Exit 0, 전 심각도 취약점 0, prod 331 / total 438 |
| Independence | Exit 0, components 8, edges 10, package files 10, scanned files 108, violations 0 |
| 공통 7범주 Gate | 303.8초, Exit 0, Overall PASS, 33 Checks, Failures 0 |
| `git diff --check` | Exit 0 |
| 승인 정본 9개 SHA-256 | 불일치 0 |
| Evidence Manifest | Source 28·Evidence 5, Hash·Byte mismatch 0 |

`npm ci`와 Independence, 공통 Gate 최초 실행은 기존 Worktree 파일에 대한 Sandbox EPERM으로 차단됐다. 코드·검사·정책을 바꾸지 않고 동일 명령을 승인 권한 환경에서 재실행해 각각 Exit 0을 확인했다. 환경 문제이며 정식 `FAILURE_REPORT`가 아니다.

## Evidence와 한계

- C03 변경 Commit: `459032773097086ac89139ee307ad53394b62208`
- 해당 정확 SHA에서 어울1이 공통 Gate를 재실행해 285.3초, Exit 0, Overall PASS, 33 Checks, Failures 0을 확인했다.
- 최종 Evidence Manifest: 9,250 bytes, SHA-256 `CDF01524FBFAC9DFF43B6AEA2C1BD3691B67E5BB5D7F985AD920E1F17C8E44D4`
- Policy SHA-256: `24A027EE54431790E87756C7A2FF70308DB56DDD2EECE4335440518BBFBB79A1`
- GitHub Run `30203630301`은 수정 전 실패 증거다.
- 수정 후 PR #18 Run `30204964560`은 Head `dc4d09cd336f61ddf664e599a70199556ad46907`, Merge Candidate `ee16aa3ad4b3da594ec683a707c48214436aa3bf`에서 6분 8초 후 PASS했다. 업로드 Artifact 기준 Overall PASS, Exit 0, Failures 0이며 기존 실패 항목 `desktop-shell-unit`과 Mobile Unit이 모두 PASS했다.
- Android/iOS Native 설치·Device는 R1-M3-05/06, Public API·Auth는 M4로 Deferred 상태를 유지한다.
- DB Migration은 `N/A`다.

어울2는 Commit·Push·PR·Merge·SSH·서버·GUI·Native Project 생성을 수행하지 않았다. 이후 어울1이 C03 Commit·Exact-SHA Local Gate·Push·GitHub 재검증을 완료했으며 Merge는 아직 수행하지 않았다.

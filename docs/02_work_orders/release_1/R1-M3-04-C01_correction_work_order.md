# R1-M3-04-C01 수정 작업지시서 — 선행 Evidence와 후속 Lockfile 책임 분리

## 1. 판정과 작업 계약

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M3-04` |
| 원 issue_id | `R1-M3-04-I001` |
| Blocker | `R1-M3-04-B001` |
| Attempt | `2` · `BLOCKED` 복구이며 FAILURE_REPORT 0회 |
| 기준 Branch/Worktree | `codex/r1-m3-04` · `C:\tmp\Daon_User-r1-m3-04` |
| 수정 목표 | 과거 Successor Commit의 Evidence 검증과 현재 후속 작업의 승인 Lockfile 변경 검증을 분리해 Fail-close 의미를 보존 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-04_attempt-2.md` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-04_progress.md` |

판정: `BLOCKED`, 실패 횟수 0회다. Mobile 구현과 전용 검증은 통과했으나 선행 검사가 이후 모든 Checkout의 Lockfile까지 과거 Successor Blob과 동일해야 한다고 요구해, R1-M3-04가 승인한 정확 의존성 추가를 영구 차단한다.

판단 이유:

1. M2-06·07의 `package-lock.json` 선행 Evidence는 지정 Origin에서 지정 Successor `8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa`로 교체된 역사적 계보를 검증한다.
2. 계보의 유효성은 Origin Blob·Successor Blob·Hash·Byte·Ancestor 관계로 판정할 수 있으며, 이후 Work Order의 현재 Working Tree/HEAD가 Successor Blob과 영원히 같을 필요는 없다.
3. 현재 Lockfile의 정확 Pin·Audit·Toolchain·재현성은 현재 Work Order의 Unit·Gate·Evidence Manifest가 소유한다.
4. 과거 기대 Hash를 현재 Lockfile Hash로 바꾸거나 Drift를 예외 처리하면 역사적 증거를 훼손하므로 금지한다.

조치: 아래의 최소 검증기 수정만 허용한 뒤 원 R1-M3-04 S6·S7을 재개한다.

## 2. 수정 계약

### 2.1 Predecessor Reconciliation

- `SUCCESSOR_SUPERSEDED`는 승인된 Origin Blob, 승인된 Successor Commit의 Artifact Blob, 고정 Hash·Byte, Origin→Successor Ancestor 관계가 모두 일치할 때만 인정한다.
- 현재 Working Tree 또는 현재 HEAD Artifact가 그 과거 Successor Blob과 같은지는 `SUCCESSOR_SUPERSEDED`의 조건이 아니다.
- 승인된 Successor Commit/Blob이 없거나 고정 Hash·Byte가 다르거나 Ancestor 관계가 아니면 계속 `UNEXPLAINED_MISMATCH`로 Fail-close 한다.
- `DIRECT_MATCH`, `LEGACY_MANIFEST_DRIFT`, 승인 Summary `90 · 80/6/4/0`의 의미와 값은 바꾸지 않는다.
- M2 Evidence Manifest, 과거 Artifact, 승인 Special Case의 Origin/Successor/Hash/Byte 값을 현재 Lockfile에 맞춰 수정하지 않는다.

### 2.2 Desktop PostCSS 회귀

- “PostCSS 8.5.23 보정 외 다른 의존성 변경 없음”은 그 보정을 수행한 고정 Successor Commit의 `package.json`·`package-lock.json` Blob을 대상으로 검증한다.
- 현재 후속 Checkout에는 Next `16.3.0-canary.93`, Vite `8.1.5`, PostCSS `8.5.23`, Vite 중첩 PostCSS 제거가 계속 유지되는지 별도로 검증한다.
- 현재 전체 non-PostCSS Package Hash가 과거 Commit과 동일하다고 요구하지 않는다. 후속 Work Order의 승인된 새 Package가 있기 때문이다.
- 고정 Successor Commit이 없거나 그 Blob의 non-PostCSS Hash가 기존 기대값과 다르면 Fail-close 한다.

### 2.3 현재 R1-M3-04 Lockfile

- 현재 Lockfile 변경은 Mobile Manifest의 정확 Pin과 일치하고 `npm ci --ignore-scripts`, Toolchain, Production Audit, Mobile Type/Bundle, 전체 Gate로 검증한다.
- Transitive Package 우연 의존, Vendor 복사, Lockfile 수동 편집, 의존성 Pin 완화로 회귀를 우회하지 않는다.
- Commit 뒤 Exact-SHA 재검증은 어울1 후속이며, 어울2는 현재 Worktree Evidence에 `pending_commit` 경계를 정직하게 기록한다.

## 3. 허용·금지 변경

이번 C01에서 추가 허용:

- `scripts/lib/predecessor-evidence-reconciliation.mjs`
- `scripts/tests/platform-prototype-evidence.test.mjs`
- `scripts/tests/desktop-tauri-shell.test.mjs`
- C01 계약 Test에 직접 필요한 Mobile 전용 Test/Script
- 지정 Progress·Attempt 2·R1-M3-04 Architecture/Evidence

계속 금지:

- Desktop Production Source·설정, M2 UI/Domain, Web, Local Service 변경
- M2/M3 과거 Evidence Manifest·Artifact·승인 Summary·고정 Origin/Successor/Hash/Byte 값 변경
- 현재 Lockfile Hash를 새로운 선행 Special Case로 추가하는 방식
- 검사 삭제·Skip·조건부 PASS·환경변수 우회·광범위 Hash 제외
- Native Project, 실제 API/Auth/DB, 범위 외 Refactor
- Commit·Push·PR·Merge·ysna-server·GUI 실행

## 4. TDD·필수 증거

먼저 다음 Test가 기존 구현에서 기대한 이유로 RED임을 확인하고 Progress에 기록한다.

1. 현재 Working Tree의 `package-lock.json`이 승인된 후속 변경으로 달라도, 고정 Successor Commit Blob 계보가 유효하면 `SUCCESSOR_SUPERSEDED` 2건을 유지한다.
2. Successor Commit 부재, Successor Blob Hash/Byte 변조, 잘못된 Ancestor 관계는 각각 `UNEXPLAINED_MISMATCH`다.
3. Desktop PostCSS 역사 검사는 고정 Successor Blob의 non-PostCSS Hash를 검증하고, 현재 Checkout은 핵심 Next/Vite/PostCSS 불변만 검증한다.

GREEN 뒤 다음을 모두 수행한다.

- 지정 원본 3 Test `40/40`
- 전체 Node 회귀 `249/249` 이상 또는 변경으로 증가한 전체 수 전부 PASS
- Mobile Lint·Type·Unit·Contract·Android/iOS Headless Production Bundle 재실행
- `npm ci --ignore-scripts`, Toolchain, Audit, Independence, 공통 7범주 Quality Gate 전부 PASS
- 과거 승인 Summary `90 · 80/6/4/0`, Legacy 4건, 고정 Successor 2건 값 불변 검사
- 범위 외 Production 파일 Diff 0, `git diff --check`, 승인 정본 Diff 0
- Architecture, Evidence 5종, Evidence Manifest, Source Hash·Byte, Attempt 2, Progress 완료

## 5. 종료 계약

Attempt 1의 `BLOCKED` 보고는 수정하지 않는다. Attempt 2에서 원 작업의 정식 결과 계약을 그대로 사용한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

다시 차단되면 새 Blocker ID와 실제 RED/현재 Diff/남은 작업을 제출한다. 완료를 위해 과거 증거나 범위를 완화하지 않는다.

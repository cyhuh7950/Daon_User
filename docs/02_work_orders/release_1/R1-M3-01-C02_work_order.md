# R1-M3-01-C02 수정 작업지시서 — Linux 이식성 Evidence 계약과 서버 검증

## 1. 수정 계약

| 항목 | 내용 |
| --- | --- |
| 원 작업 | `R1-M3-01` |
| 수정 작업 | `R1-M3-01-C02` |
| issue_id | `R1-M3-01-I001` 유지 |
| 발견 단계 | PR #15 GitHub Gate 통과 후 ysna-server exact SHA 검증 |
| 판정 | `REJECT(C2, 중대 미진)` — Linux 이식성 검증 실패 |
| 개발자 | 동일 어울2 · 단일 Writer |
| Branch/Worktree | `codex/r1-m3-01` · `C:\tmp\Daon_User-r1-m3-01` |
| 기준 Commit | `428199d32e9301df5f0441bf3b610ac48a469dfc` |
| 서버 Checkout | `/home/ubuntu/deploy/daon-user/R1-M3-01/428199d32e9301df5f0441bf3b610ac48a469dfc` |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M3-01_progress.md`에 C02 구간 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-01-C02_attempt-1.md` |

원 R1-M3-01·C01 작업지시서와 지정 정본 전체를 다시 적용한다. C02는 아래 이식성 결함만 수정하며 Production Web Shell, BFF, Session 복원 동작과 M2 정본을 변경하지 않는다.

## 2. 재현된 결함과 확정 원인

### C2-1 Raw 작업트리 Hash의 OS 종속성

- Windows 작업트리의 `packages/contracts/navigation.json`은 CRLF 5,378 byte, SHA-256 `4FB547...`이다.
- 같은 Git Commit의 Linux Checkout은 LF 5,360 byte, SHA-256 `A328A3...`이다.
- `scripts/tests/web-runtime-shell.test.mjs`가 Windows Raw Hash를 불변값으로 고정해 Linux에서 같은 Git 내용을 거부했다.
- R1-M3-01 Manifest도 같은 방식으로 기존 추적 파일 3건을 Windows Raw Hash로 기록해 Linux 전수 검증에서 불일치했다.

### C2-2 서버 검증 이미지의 Git 실행 계약 누락

- `node:24.18.0-bookworm-slim`에는 Git 실행 파일이 없다.
- 선행 Reconciliation이 내부 Git 조회 오류를 fail-close하여 `INVALID_OR_MISSING_ORIGIN_COMMIT`로 종료했다.
- 표준 `node:24.18.0-bookworm`, Git `safe.directory=/workspace`에서는 동일 선행 테스트 19/19가 통과했다.
- 제품 계보 구현의 결함이 아니므로 해당 구현은 변경하지 않는다.

## 3. 필수 수정

### 3.1 Portable Evidence 표현

- UTF-8 Text Artifact는 엄격한 UTF-8 Round-trip이 성립할 때 `CRLF → LF`만 수행한 `portable_utf8_lf` 표현의 SHA-256과 Byte를 기록한다.
- Lone CR, Unicode 정규화, Trim, 공백·JSON 재직렬화 등 다른 변환은 금지한다.
- PNG/JPG 등 Binary Artifact는 `raw` 표현의 SHA-256과 Byte를 유지한다.
- Manifest schema를 올리고 각 Artifact에 `representation`을 명시한다.
- Manifest validator는 선언된 표현 외 값을 fail-close하고, 파일 부재·UTF-8 Round-trip 실패·Hash 또는 Byte 단독 일치를 모두 실패시킨다.
- Manifest 자체와 Progress는 계속 `mutable_handoff_records`로 순환 Hash에서 제외한다.
- C02 결과보고를 Artifact에 추가하고 전체 Artifact를 새 계약으로 재결속한다.

### 3.2 M2 정본 불변 Test 이식성

- `web-runtime-shell.test.mjs`의 Windows Raw Hash 비교를 동일 `portable_utf8_lf` 규칙으로 교체한다.
- 같은 논리 내용의 LF·CRLF가 동일 표현이 됨을 Test한다.
- 내용 변경, Lone CR, 비UTF-8 또는 허용되지 않은 표현은 실패함을 Test한다.
- Navigation, Screen, Token, M2 Model/Reducer 불변 검증의 강도는 낮추지 않는다.

### 3.3 서버 실행 계약

- 서버 검증은 `node:24.18.0-bookworm`을 사용한다. `bookworm-slim`은 사용하지 않는다.
- 컨테이너 안에서 npm `11.12.1`을 고정하고 `git --version`을 확인한다.
- Mount된 `/workspace`에만 `git config --global --add safe.directory /workspace`를 적용한다.
- 일회성 `--rm` 컨테이너만 사용하고 기존 `shared-db`, `common`, `netdata`, `proxy`, Network, Volume을 사용하거나 변경하지 않는다.
- DB·Migration은 `N/A`, 외부 효과는 0건이다.

## 4. 허용·금지 범위

허용:

- `scripts/tests/web-runtime-shell.test.mjs`
- 필요 시 R1-M3-01 전용 Portable Manifest validator/helper와 전용 Test
- `docs/03_evidence/release_1/R1-M3-01/evidence-manifest.json`
- `docs/01_architecture/web_runtime_shell_contract.md`의 Evidence 표현 계약
- C02 결과보고와 기존 Progress

금지:

- `apps/web/**`, Runtime/BFF 경로·응답·UI 동작 변경
- `packages/ui/src/**` 제품 코드 변경
- M2 Model/Reducer, Navigation, Screen, Token 정본 변경
- 선행 Reconciliation 구현·승인 계보·Legacy 특별 규칙 변경
- Dependency·Lockfile·Toolchain·CI·공개 API·데이터 계약 변경
- CRLF 외 정규화, 허용값 확대, Hash-only 또는 Byte-only 수락
- Commit·Push·PR·Merge 수행

## 5. 수행 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| C02-S0 | 원 결과·서버 실패·현재 Clean Diff·Process/Port 확인 | Progress |
| C02-S1 | Linux Raw Hash와 Git 없는 Slim Image 실패를 재현·고정 | RED Evidence |
| C02-S2 | Portable 표현 validator/Test 선작성 | 유효 RED |
| C02-S3 | Test·Manifest·Contract 최소 수정 | 전용 GREEN |
| C02-S4 | Windows 전용·전체 순차 회귀·Lint·Build·Gate | 전부 PASS |
| C02-S5 | C02 보고·Manifest 재결속·전수 검증 | N/N |
| C02-S6 | 쓰기 중지·정식 상태 반환 | 결과보고 |

각 단계의 착수·완료·오류·복구·테스트·종료 직전에 기존 Progress를 갱신한다.

어울2의 로컬 Green 후 Commit·Push는 어울1이 수행한다. 새 Commit SHA의 ysna-server 검증은 어울1이 수행하며, 서버 명령과 결과를 최종 Progress에 추가한다.

## 6. 완료 조건

- Windows와 Linux에서 같은 Git Text Artifact가 같은 `portable_utf8_lf` SHA-256·Byte로 검증된다.
- Binary는 Raw Hash·Byte 전수 일치한다.
- M2 정본 불변 검사가 OS 줄바꿈 차이만 허용하고 실제 내용 변경은 거부한다.
- 원 Web Shell/Hydration 전용 Test와 전체 순차 회귀가 PASS한다.
- Lint·Toolchain·Independence·Fresh Build·Quality Gate가 PASS한다.
- Manifest가 C02 보고 포함 전체 Artifact를 새 표현으로 N/N 결속한다.
- 제품 코드·Lockfile·Dependency·Toolchain·CI Diff 0.
- 최종 제품 Process·4179/4180 Port 0, 외부 효과 0, DB Migration N/A.

결과보고 첫 줄:

```text
COMPLETED | R1-M3-01-I001 | C02 Linux 이식성 Evidence 계약 | 변경 파일 | RED·GREEN·Manifest 근거 | 미해결 사항 | 어울1 재검토 요청
```

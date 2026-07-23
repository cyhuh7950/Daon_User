# R1-M3-01-C02-C01 수정 작업지시서 — Canonical Artifact 경로 결속

## 1. 수정 계약

| 항목 | 내용 |
| --- | --- |
| 원 수정 작업 | `R1-M3-01-C02` |
| 보완 작업 | `R1-M3-01-C02-C01` |
| issue_id | `R1-M3-01-I001` 유지 |
| 판정 | 독립 검토 `REJECT(C2, 중대 미진)` |
| 개발자 | 동일 어울2 · 단일 Writer |
| Branch/Worktree | `codex/r1-m3-01` · `C:\tmp\Daon_User-r1-m3-01` |
| 기준 Commit | `428199d32e9301df5f0441bf3b610ac48a469dfc` + 미Commit C02 변경 보존 |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M3-01_progress.md`에 C02-C01 구간 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-01-C02-C01_attempt-1.md` |

원 R1-M3-01·C01·C02 작업지시서와 지정 정본 전체를 다시 적용한다. C02-C01은 아래 Canonical 경로 결함만 수정하며 Portable 표현 의미와 제품 코드는 변경하지 않는다.

## 2. 확정 결함

현재 Validator는 중복 여부를 정규화 전 원문 `artifact.path` 문자열로 비교한다. 다음 두 항목이 같은 실제 파일인데도 Manifest를 `valid=true`로 수락한다.

```text
apps/web/app/layout.jsx
apps/web/app/../app/layout.jsx
```

이 별칭으로 `artifact_count`를 부풀리고 필수 Artifact 누락을 숨길 수 있으므로 C2다.

## 3. 필수 수정

### 3.1 Canonical 경로 계약

- Manifest Artifact 경로는 `/` 구분자를 사용하는 정본 Repository 상대경로만 허용한다.
- 빈 경로, 절대경로, `.`·`..` Segment, 선행 `./`, 중복 구분자, 역슬래시 별칭을 fail-close한다.
- Root와 존재하는 Artifact를 `fs.realpathSync.native` 또는 동등한 실제 경로로 해석한다.
- 실제 Artifact가 실제 Root 밖이면 Symlink·Junction을 포함해 `ARTIFACT_OUTSIDE_ROOT`로 거부한다.
- Manifest 경로와 Root 기준 실제 Canonical 상대경로가 일치하지 않으면 `NON_CANONICAL_ARTIFACT_PATH`로 거부한다.
- 중복 키는 Canonical Real Path 기준으로 생성한다. Windows에서는 대소문자를 접어 비교한다.
- 일반 파일만 허용하며 Directory·비정상 파일 유형은 거부한다.

### 3.2 공격 Test

기존 `portable-evidence.test.mjs`에 최소한 다음 RED→GREEN을 추가한다.

- `a/../a` 별칭
- `./a` 별칭
- 역슬래시 Separator 별칭
- Windows 대소문자 별칭 또는 OS 독립적으로 주입 가능한 Case-fold 중복 판정
- Root 안 경로가 Root 밖 파일을 가리키는 Symlink/Junction
- Canonical 경로로 선언된 정상 Artifact

공격 입력은 실패 코드까지 확인한다. 환경이 Symlink 권한을 주지 않는 경우 Windows Directory Junction으로 Root 밖 Escape를 실제 재현하며 Test를 생략하지 않는다.

### 3.3 Manifest·보고 재결속

- 기존 Manifest 24건의 Canonical 경로가 모두 Green인지 확인한다.
- C02-C01 결과보고를 추가해 Manifest를 25건으로 재결속한다.
- Manifest 자체와 Progress의 순환 Hash 제외는 유지한다.
- Portable Text/Binary 표현·Hash·Byte 의미는 변경하지 않는다.

## 4. 허용·금지 범위

허용:

- `scripts/lib/portable-evidence.mjs`
- `scripts/tests/portable-evidence.test.mjs`
- `docs/01_architecture/web_runtime_shell_contract.md`의 Canonical 경로 문장
- R1-M3-01 Manifest, C02-C01 결과보고, 기존 Progress

금지:

- `apps/web/**`, `packages/ui/src/**` 제품 코드
- `web-runtime-shell.test.mjs`의 이미 Green인 Portable 정본 Hash
- Predecessor Reconciliation, M2 정본, Dependency·Lockfile·Toolchain·CI
- Unicode·Trim·공백·JSON 재직렬화 또는 Portable 표현 완화
- Commit·Push·PR·서버 배포

## 5. 수행 단계와 완료 조건

1. 독립 검토 공격과 어울1 재현 `valid=true`를 Progress에 고정한다.
2. 경로 별칭·Case·Junction 공격 Test를 선작성하고 유효 RED를 확인한다.
3. Canonical/Real Path 검사를 최소 구현해 공격 Test와 기존 Portable Test를 GREEN으로 만든다.
4. 집중 Test, 전체 순차 회귀, Lint, Toolchain, Independence, Clean Build, Gate를 재실행한다.
5. C02-C01 보고서를 포함한 Manifest 25/25를 검증한다.
6. 금지 범위 Diff 0, Lockfile Diff 0, 4179/4180 Port 0을 확인하고 쓰기를 중지한다.

결과보고 첫 줄:

```text
COMPLETED | R1-M3-01-I001 | C02-C01 Canonical Artifact 경로 결속 | 변경 파일 | RED·GREEN·Manifest 근거 | 미해결 사항 | 어울1 재검토 요청
```

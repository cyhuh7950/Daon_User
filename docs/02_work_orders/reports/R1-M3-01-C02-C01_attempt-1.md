COMPLETED | R1-M3-01-I001 | C02-C01 Canonical Artifact 경로 결속 | 변경 파일 | RED·GREEN·Manifest 근거 | 미해결 사항 | 어울1 재검토 요청

# R1-M3-01-C02-C01 결과보고

## 판정

- `COMPLETED`
- Manifest Artifact를 원문 경로가 아닌 Canonical Real Path에 결속했다.
- 경로 별칭, Windows 대소문자 중복, Symlink·Junction Root Escape와 일반 파일이 아닌 Artifact를 fail-close한다.
- Portable Text/Binary 표현과 제품 동작은 변경하지 않았다.

## 판단 이유

### 확정 결함과 RED

- 기존 Validator는 원문 `artifact.path`로만 중복을 검사하고 `path.resolve` 결과만 Root 내부인지 확인했다.
- 따라서 `apps/web/app/layout.jsx`와 `apps/web/app/../app/layout.jsx`가 같은 실제 파일이어도 별도 Artifact로 수락될 수 있었다.
- 공격 Test 선작성 결과 기존 Portable 4건은 PASS하고 다음 공격 3건이 정확히 FAIL했다.
  - `a/../a`, 선행 `./`, 역슬래시·중복 구분자: `NON_CANONICAL_ARTIFACT_PATH` 누락
  - OS 독립 주입 Windows Case-fold: `DUPLICATE_ARTIFACT_PATH` 누락
  - 실제 Windows Directory Junction Root Escape: `ARTIFACT_OUTSIDE_ROOT` 누락
- RED 결과: `4/7 PASS`, 공격 3 FAIL, skip 0

### 최소 수정

- `scripts/lib/portable-evidence.mjs`
  - `/` 구분자의 빈 Segment 없는 Repository 상대 Canonical 경로만 허용
  - Root와 Artifact를 `fs.realpathSync.native`로 해석
  - 실제 Root 밖 경로를 `ARTIFACT_OUTSIDE_ROOT`로 거부
  - 실제 Root 기준 Canonical 상대경로와 Manifest 경로 불일치를 `NON_CANONICAL_ARTIFACT_PATH`로 거부
  - Canonical Real Path 중복 Key 사용, Windows에서는 대소문자 접기
  - 일반 파일만 허용
- `scripts/tests/portable-evidence.test.mjs`
  - 정상 Canonical, 경로 별칭, 주입 Case-fold, 실제 Junction Escape 공격 검증
- `docs/01_architecture/web_runtime_shell_contract.md`
  - Canonical/Real Path와 일반 파일 계약 추가
- Manifest, 본 보고서, Progress만 증거 범위에서 갱신

### GREEN과 회귀

- Portable 공격 포함: `7/7 PASS`
- Portable + Web Shell + Hydration 집중 검증: `17/17 PASS`
- 전체 순차 회귀: `203/203 PASS`
- Workspace Lint: `11 files PASS`
- Toolchain: `7 manifests exact PASS`
- Independence: `55 files, 0 violations`
- Clean Production Build: `PASS`, 8 routes
- Quality Gate: 7 categories `PASS`, failures 0
- 공통 Gate가 재생성한 과거 R1-M1-05 Evidence 2건은 HEAD로 원상 복원했다.

## 변경·비변경 범위

- 변경: Portable Helper/Test, Web Runtime Shell Contract의 Canonical 경로 문장, R1-M3-01 Manifest, 본 보고서, Progress
- 비변경: `apps/web/**`, `packages/ui/src/**`, `web-runtime-shell.test.mjs` Portable 정본 Hash, Predecessor Reconciliation, M2 정본, Dependency·Lockfile·Toolchain·CI
- Commit, Push, PR, Server 배포는 수행하지 않았다.

## 미해결 사항

- 로컬 구현 미해결 사항은 없다.
- 새 Commit SHA의 Linux 서버 재검증은 기존 계약대로 어울1 소유 후속 검증이다.

## 조치

- 어울1이 독립 공격 재검토 후 Commit 여부를 판단한다.
- 승인·Commit 이후 지정 Bookworm 일회성 Container 계약으로 Linux 서버를 재검증한다.

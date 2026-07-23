COMPLETED | R1-M3-01-I001 | C02 Linux 이식성 Evidence 계약 | 변경 파일 | RED·GREEN·Manifest 근거 | 미해결 사항 | 어울1 재검토 요청

# R1-M3-01-C02 결과보고

## 판정

- `COMPLETED`
- Windows 작업트리의 CRLF와 Linux/Git의 LF가 동일한 텍스트 내용일 때 동일하게 검증되도록 `portable_utf8_lf` 표현을 추가했다.
- PNG/JPG 등 Binary Evidence는 변환 없이 `raw` Byte 기준을 유지한다.
- 제품 코드, 선행 Predecessor Reconciliation 구현, M2 정본, 의존성·Lockfile·Toolchain·CI는 변경하지 않았다.

## 판단 이유

### 원인과 범위

- 확정 원인은 Windows Raw Byte Hash를 Linux LF 작업트리에서 그대로 비교한 OS 표현 차이였다.
- `navigation.json`은 Windows Raw `5378 bytes / 4FB54727...`와 Git·portable `5360 bytes / A328A388...`로 달랐고, Web Shell 불변 정본 9건은 모두 CRLF→LF 후 Git Blob과 일치했다.
- 선행 Reconciliation 구현의 비교 의미나 제품 동작을 바꿀 필요는 없었다.

### 변경 파일

- `scripts/lib/portable-evidence.mjs`
  - `portable_utf8_lf`와 `raw` Digest
  - strict UTF-8 round-trip, Lone CR 거부, CRLF→LF만 허용
  - Manifest Schema·Artifact 수·중복 경로·경로 탈출·Hash·Byte·확장자별 표현 fail-close
  - CLI 검증 진입점
- `scripts/tests/portable-evidence.test.mjs`
  - LF/CRLF 동등성, 내용 변경, Lone CR, 비UTF-8, 미지원 표현, Binary raw, Manifest fail-close 검증
- `scripts/tests/web-runtime-shell.test.mjs`
  - Web Shell 불변 정본 9건을 `portable_utf8_lf` SHA-256으로 검증
- `docs/01_architecture/web_runtime_shell_contract.md`
  - Text/Binary 표현, 금지 변환, Manifest 검증, Linux 서버 실행 계약
- `docs/03_evidence/release_1/R1-M3-01/evidence-manifest.json`
  - Schema `2.0`, Artifact별 `representation`, C02 Helper·Test·보고서를 포함한 24건 결속
- 본 보고서와 진행 복구 기록

### RED·GREEN·회귀 근거

- RED: `node --test scripts/tests/portable-evidence.test.mjs` → `0/4 PASS`, 4건 모두 미구현 API를 검출
- GREEN: 동일 명령 → `4/4 PASS`
- 집중 검증: Portable 4 + Web Shell 6 + Hydration 4 → `14/14 PASS`
- 전체 순차 회귀: `200/200 PASS`
- Workspace Lint: `11 files PASS`
- Toolchain: `7 manifests exact PASS`
- Independence: `55 files, 0 violations`
- Clean Production Build: `PASS`, 8 routes
- Quality Gate: 7 categories `PASS`, failures 0
- 공통 Gate가 재생성한 과거 R1-M1-05 Evidence 2건은 검증 후 HEAD로 원상 복원했다.

### Linux 서버 재검증 계약

- Image: `node:24.18.0-bookworm`
- npm: `11.12.1`
- Git이 포함되어야 하며 `/workspace`를 `safe.directory`로 등록한다.
- 실행은 `--rm` 일회성 Container만 사용한다.
- 기존 Service·Network·Volume·DB를 생성·연결·변경하지 않는다.
- `bookworm-slim` 대체, 기존 운영 Container 재사용, 내부 주소의 Browser 노출은 금지한다.

## 조치

- 로컬 구현·자동 검증·Manifest 결속까지 완료했다.
- Commit, Push, PR, Server 배포는 수행하지 않았다.
- 새 Commit SHA 기준 Linux 서버 검증은 작업지시대로 어울1이 수행한다.

## 미해결 사항

- 로컬 구현 미해결 사항은 없다.
- 새 Commit SHA가 아직 없으므로 Linux 서버 재검증 결과는 의도적으로 미생성 상태다. 이는 어울1 소유의 후속 검증 항목이며 C02 구현 결함이 아니다.

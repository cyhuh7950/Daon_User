# R1-M1-05 ysna-server 검증 요약

## 판정

`SERVER_VALIDATION_PASS`

이 판정은 불변 Git SHA의 ysna-server 격리 검증에만 적용한다. GitHub Actions 실제 Run과 Repository Branch Protection/Required Check 확인은 확보되지 않았으므로 R1-M1-05 전체 완료 판정과 혼합하지 않는다.

## 검증 대상

- Work Order / issue_id: `R1-M1-05` / `R1-M1-05-I001`
- 불변 Git SHA: `3b0f03fec28fd545b34130c1a0c6fae68efeda15`
- 격리 경로: `/home/ubuntu/deploy/daon-user/R1-M1-05/3b0f03fec28fd545b34130c1a0c6fae68efeda15`
- Architecture: Host `aarch64`, Docker `arm64`
- Checkout: 최초·최종 `CLEAN`, `DETACHED`

## 서버 검증 결과

| 항목 | 결과 |
| --- | --- |
| Toolchain | Node `24.18.0`, npm `11.12.1`, Corepack `0.35.0`, uv `0.11.2`; 승인 Pin 일치 |
| Lockfile / Pin | `package-lock.json`, `uv.lock`, `toolchain-versions.json` Git Blob SHA-256 일치 |
| 설치 | `npm ci` Exit `0`, 258 Packages |
| Runner Test | `25/25 PASS`, Exit `0` |
| 공통 품질 Gate | 7개 범주 명시, Overall `PASS`, Exit `0`, Failures `0` |
| 독립성 | 8 Components, 10 Edges, Violations `0` |
| Artifact 독립 검증 | Hash 근거 8건, 경로 근거 17건 전부 확인 |
| Schema / Migration | `NOT_APPLICABLE_NO_SCHEMA`; Schema/Migration 경로·Manifest 참조 0, DB/Migration 명령 미실행 |
| 서버 자원 불변 | Container·Network·Volume 사전/사후 SHA-256 각각 동일, 변경 `0` |
| 임시 자원 | 잔존 임시 Container `0`, Listen Port 생성 `0` |

## Artifact

- Result: `server-validation-quality-gate-result.json`
  - SHA-256: `D12955B6CD8B39B30FE32AAC4C600CD48759AB6F0C1A1697EE6480A4743891FE`
- Summary: `server-validation-quality-gate-summary.md`
  - SHA-256: `45139F6343BBCCA5BBCC826964F8ACFB77B6EDD799BE50ACFBA7B289135C5DDA`
- Manifest: `server-validation-manifest.json`

## 복구 및 정리

- 최초 공통 Gate Artifact의 `git_sha=UNAVAILABLE` 결과는 최종 증거에서 제외했다.
- Git이 포함된 일회성 ARM64 Container로 정확 SHA Artifact를 재생성했고, Client 대기 중단 뒤 재실행하지 않고 새 Timestamp·정확 SHA·PASS 결과·독립 Artifact 검증으로 완료를 확인했다.
- 일회성 Evidence Validator는 제품 구현물이 아니며 서버 `.server-tools`에서 제거했다. 서버 Checkout은 최종 `CLEAN`이다.
- 공식 ARM64 검증 Image 2개는 Docker Image Cache에 남아 있으나 Container·Network·Volume·Port와 기존 Service를 변경하지 않았다.

## 제한

- GitHub Actions 실제 Run과 Repository Branch Protection/Required Check 설정 증거는 어울1 측 접근 제한으로 미확보다.
- 이를 서버 PASS로 대체하거나 우회하지 않았으며 PR·Repository 설정도 변경하지 않았다.

BLOCKED | R1-M2-06-DEP-001 | Sharp·PostCSS 보안 의존성 보정 조사 | SEC01 변경을 제거해 package.json·package-lock.json을 착수 전 상태로 복원하고 격리 재현 증거를 기록했다 | Override Fresh Lock은 Audit 0이나 npm ls invalid/ELSPROBLEMS, 기존 Lock은 취약 버전 유지 | 안정판 Upstream 의존성 범위가 승인 안전 버전을 수용하지 않음 | 어울1이 안정판 대기 또는 Canary 도입 별도 승인 여부 판단

# R1-M2-06 SEC01 Dependency Remediation Attempt 1

## 판정

`BLOCKED` — Upstream·설계 판단 필요.

승인된 `sharp@0.35.3`, `postcss@8.5.21`을 npm Override로 고정하면 Fresh Lock의 Audit은 해소되지만 Next가 선언한 Dependency 범위와 충돌해 npm Tree 무결성 검사를 통과하지 못한다. Audit 예외나 강제 설치 없이 현재 안정판에서 SEC01 완료 조건을 함께 만족할 수 없다.

## 수행한 작업과 증거

### 착수 기준선

- Branch/HEAD: `codex/r1-m2-06` / `d5e0a09afc2ed110fbd98cbf870a7a75b36af8ed`
- Before Tree: `next@16.2.10` → `sharp@0.34.5`, `postcss@8.4.31`
- SEC01 착수 전 `package.json`, `package-lock.json` Diff 0
- 승인 정본 6개 SHA-256 일치

### 기존 Lock 재해석

Flat Root와 Parent 한정 `overrides.next` 모두 승인 exact 버전만 사용했다.

- 사용자 npm Cache는 mkdir EPERM이 발생해 `C:\tmp\daon-user-npm-cache`로 격리했다.
- 격리 Cache의 package-lock-only install/update는 Exit 0이었다.
- 기존 Lock은 변경되지 않았고 `npm query ':overridden'`은 `[]`였다.
- Tree는 `sharp@0.34.5`, `postcss@8.4.31`을 유지했다.
- Repo `package-lock.json` 또는 `node_modules`를 삭제하지 않았다.

### Fresh Lock 격리 재현

저장소 밖 고유 경로에 루트와 Workspace `package.json` 7개만 복사했다. Source, 기존 Lock, node_modules는 복사하지 않았다.

| 방식 | 경로 | Exact Tree | Audit | npm ls | query overridden |
| --- | --- | --- | --- | --- | --- |
| Parent 한정 | `C:\tmp\daon-user-sec01-lock-repro-20260722-1108` | sharp 0.35.3, postcss 8.5.21 | 전체 0, Exit 0 | 둘 다 `invalid`, `ELSPROBLEMS` | `[]` |
| Flat Root | `C:\tmp\daon-user-sec01-lock-repro-flat-20260722-1120` | sharp 0.35.3, postcss 8.5.21 | 전체 0, Exit 0 | 둘 다 `invalid`, `ELSPROBLEMS` | `[]` |

Fresh Lock의 Registry Host는 `registry.npmjs.org`만 존재했고 Sharp·PostCSS·`@img/*` Integrity 누락은 0건이었다. 그러나 Next 16.2.10의 선언은 `postcss=8.4.31`, `sharp=^0.34.5`이므로 승인 버전을 비정상 Tree로 판정했다.

Fresh Lock은 기존 318개 Version Node에서 320개로 증가했다. 허용 범위인 Sharp·`@img/*`·libvips·PostCSS 변화 외에 다음 승인 외 Drift가 발생해 Repo에 수용하지 않았다.

- `browserslist` 4.28.6 → 4.28.7
- `electron-to-chromium` 1.5.393 → 1.5.395
- `baseline-browser-mapping` 2.10.43 → 2.11.0

격리 Fresh Lock을 Repo에 복사하지 않았다.

## Upstream 선택지

공식 npm Manifest를 다시 확인했다.

- 안정판 `next@16.2.11`: `postcss=8.4.31`, optional `sharp=^0.34.5`로 동일 문제가 해소되지 않는다.
- `next@16.3.0-canary.93`: `postcss=8.5.10`, optional `sharp=^0.35.3`으로 안전 범위를 선언하지만 Canary 도입은 현재 승인 범위 밖의 직접 Dependency·중요 위험 변경이다.

선택지는 안정판 Upstream이 안전 범위를 선언할 때까지 기다리거나, Canary 도입을 별도 위험 승인·작업지시로 검토하는 것이다. 이번 작업에서는 Canary, 다른 버전, Audit 예외를 적용하지 않았다.

## 최종 변경 상태

- SEC01 Override 변경을 정확히 제거했다.
- `package.json`, `package-lock.json` Diff 0을 재확인했다.
- C02 기능 코드·Browser PNG/JSON을 수정하지 않았다.
- 보호 Dirty R1-M1-04 두 파일을 수정·복원·Stage하지 않았다.
- Commit·Push·ysna-server·PR·Merge를 수행하지 않았다.

## 미해결 사항과 다음 판단

현재 안정판 Upstream 계약으로 Audit 0과 정상 npm Tree를 동시에 충족할 수 없다. 어울1은 신산님 승인 경계에 따라 안정판 대기 또는 Canary 별도 평가 작업 여부를 결정해야 한다.

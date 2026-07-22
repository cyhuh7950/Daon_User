# Next Canary 임시 보안 브리지

## 결정

| 항목 | 내용 |
| --- | --- |
| 결정 ID | `R1-D022` · `CHG-R1-M2-06-SEC02-001` |
| 승인 | 신산님 · `APR-R1-M2-06-SEC02-20260722-01` |
| 적용 버전 | `next@16.3.0-canary.93` exact |
| 적용 범위 | Release 1 로컬 개발·검증, GitHub Required Check, ysna-server 격리 테스트 |
| 소유자 | 어울1 · 설계·기술 책임자 |
| 상태 | 임시 보안 브리지 · 안정판 전환 의무 |

## 이유와 적용 경계

안정판 Next 16.2.10과 16.2.11은 취약한 `postcss@8.4.31`과 `sharp@^0.34.5` 범위를 선언한다. npm Override는 Audit을 해소하지만 `npm ls`에서 `invalid/ELSPROBLEMS`가 되어 정상 Dependency Tree 계약을 만족하지 못했다.

Next 16.3.0-canary.93은 `postcss@8.5.10`, `sharp@^0.35.3`을 공식 Dependency로 선언한다. 승인된 exact Canary 한 건과 그 필수 Lock Closure만 사용하며 Root Override·Audit 예외·강제 설치는 사용하지 않는다.

## 위험과 금지사항

- Canary API·Build·Runtime 회귀 가능성이 안정판보다 높다.
- 안정판으로 교체하기 전 실제 운영 Release를 금지한다.
- ysna-server는 격리 검증 배포에만 사용하며 운영 배포로 간주하지 않는다.
- 다른 Canary, Root Override, `--force`, `--legacy-peer-deps`, Audit Suppress·Allowlist를 금지한다.
- React·React DOM·Node·npm·TypeScript 및 기능 코드의 동반 변경을 금지한다.

## 검증 계약

- 정상 Tree: Next 16.3.0-canary.93, PostCSS 8.5.10, Sharp 0.35.3
- Production Dependency Audit 전 등급 0
- Sharp Runtime 0.35.3, libvips 8.18.3 이상
- 전용 21/21, 전체 선택 98/98, Lint, Production Build, Route, Runtime HTTP/DOM/Console/same-origin Smoke
- 공통 Quality Gate 전체 범주 PASS
- Windows 로컬 후 exact Git SHA로 ysna-server ARM64 동일 검증

## 종료 조건

안전한 PostCSS·Sharp 범위를 공식 선언한 안정판 Next가 출시되면 어울1이 안정판 후보를 고정한다. 동일한 Dependency Tree·Audit·21/98·Lint·Build·Runtime Smoke·공통 Gate를 모두 통과한 뒤 즉시 안정판으로 교체하고 R1-D022를 종료 상태로 갱신한다.

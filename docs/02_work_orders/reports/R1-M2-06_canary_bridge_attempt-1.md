COMPLETED | R1-M2-06-DEP-002 | Next Canary 임시 보안 브리지 적용 | Next exact·필수 Lock Closure·Toolchain 정본·R1-D022·Runtime Evidence·Manifest | Tree·Audit·21/21·98/98·Lint·Build·Runtime Smoke·공통 Gate 전부 PASS | 안정판 전환 전 운영 Release 금지와 Canary 고유 위험 | 어울1 독립 검토 후 Commit·Push·ysna-server ARM64 검증

# R1-M2-06 SEC02 Canary Bridge Attempt 1

## 판정

`COMPLETED` · 로컬 개발·검증용 `HANDOFF_READY_CANARY_BRIDGE`.

승인된 `next@16.3.0-canary.93` exact 한 건으로 정상 Dependency Tree와 Production Dependency Audit 0을 달성했다. 이 결과는 운영 Release 승인이 아니며 안전한 안정판 전환 전 운영 배포를 금지한다.

## 변경 결과

- `apps/web/package.json`: Next 16.2.10 → 16.3.0-canary.93 exact
- `package-lock.json`: Next·`@next/*`·PostCSS·Sharp·`@img/*`·libvips 필수 Closure
- `toolchain-versions.json`: Gate 정본의 Next 값만 승인 Canary와 동기화
- `docs/01_architecture/DECISIONS.md`: R1-D002를 보존하고 후속 R1-D022·변경 기록 추가
- `docs/01_architecture/temporary_next_canary_security_bridge.md`: 범위·위험·운영 금지·검증·종료 조건
- `docs/03_evidence/release_1/R1-M2-06/canary-runtime-smoke.json`
- Evidence Manifest·Progress·이 결과보고

Root Override, 다른 Direct Dependency, 기능 코드, UI, Route, API, 데이터 계약, Quality Gate·CI·검증 로직은 변경하지 않았다.

## Dependency·공급망 검증

| 항목 | 결과 |
| --- | --- |
| Targeted Install | Exit 0, added 2·changed 6 |
| Lock Version Nodes | 318 → 320; 추가 2, 변경 36, 제거 0 |
| 승인 외 Version Drift | 0 |
| clean npm ci | npm 최종 Log Exit 0 · `info ok` |
| npm ls | Exit 0; invalid·extraneous 0 |
| Exact Tree | Next 16.3.0-canary.93 · PostCSS 8.5.10 · Sharp 0.35.3 |
| Registry·Integrity | registry.npmjs.org만 사용 · Next/@next/Sharp/@img/PostCSS 누락 0 |
| Sharp Runtime | Sharp 0.35.3 · libvips 8.18.3 |
| npm audit | Info/Low/Moderate/High/Critical 전부 0 · Exit 0 |

OneDrive clean npm ci는 도구 wrapper가 1204초에 종료됐으나 하위 npm PID는 계속 실행됐다. CPU·Working Set·node_modules mtime을 확인하며 임의 종료하지 않았고, 최종 npm debug Log의 Exit 0과 `info ok`를 수집했다. 재실행으로 성공을 위장하지 않았다.

## 기능·Runtime 회귀

| 검증 | 결과 |
| --- | --- |
| Account/Security 전용 | 21/21 PASS |
| 전체 선택 회귀 | 98/98 PASS |
| Workspace Lint | 11 files PASS |
| Production Build | PASS · Next 16.3.0-canary.93 |
| Route Build | Account·Organization Static, Workspace Dynamic PASS |
| Toolchain 단독 | 7 manifests·exact pins·lockfiles PASS |
| 공통 Quality Gate | Overall PASS · Exit 0 · Failures 0 · 전 범주 PASS |

첫 공통 Gate는 승인 Canary와 기존 Toolchain 정본 Next 16.2.10의 불일치로 `Next mismatch` 한 건만 실패했다. 어울1 기술 판단에 따라 검증을 완화하지 않고 `toolchain-versions.json`의 Next 값만 동기화했으며, 단독 Toolchain과 공통 Gate 재실행이 통과했다.

Standalone Production Runtime Smoke 결과:

- `/settings/account`, `/settings/organization`, `/workspaces/workspace-release-one` HTTP 200
- Title·H1·Route ID·Screen ID 정본 일치
- Browser Console warning/error 0
- 각 Route Resource 11개, non-same-origin 0, API-like Resource 0
- Standalone stderr 0, 종료 후 Port 4310 Listener 0

기존 C01/C02 PNG 네 개와 `browser-validation.json`은 다시 쓰지 않았고 Hash가 그대로임을 대조했다.

## 운영 경계와 남은 위험

- Canary는 Release 1 개발·검증과 ysna-server 격리 테스트에만 사용한다.
- 안정판 전환 전 실제 운영 Release를 금지한다.
- Canary 고유 API·Build·Runtime 회귀 가능성은 남아 있다.
- 안전한 PostCSS·Sharp 범위를 선언한 안정판 Next가 출시되면 동일 Tree·Audit·21/98·Lint·Build·Runtime Smoke·공통 Gate를 통과한 뒤 즉시 교체한다.

## 인계

- 어울1: 최신 Diff 읽기 전용 독립 검토
- 이후 어울1 권한으로 정확한 허용 파일 Commit·Push, GitHub Required Check, ysna-server exact SHA ARM64 동일 검증
- 어울2는 Commit·Push·ysna-server·PR·Merge를 수행하지 않았다.
- 보호 Dirty R1-M1-04 두 파일은 수정·복원·Stage하지 않았다.

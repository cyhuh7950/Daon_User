# R1-M2-06-SEC02 보안 작업지시서 — Next Canary 임시 보안 브리지

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| 연계 Work Order | `R1-M2-06` |
| issue_id | `R1-M2-06-DEP-002` |
| 승인 | 신산님 승인 · 2026-07-22 |
| 목적 | 정상 Dependency Tree로 Sharp·PostCSS 취약점을 제거하면서 R1-M2-06 개발을 계속 진행 |
| 승인 버전 | `next@16.3.0-canary.93` exact |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| 기준 Branch/HEAD | `codex/r1-m2-06` · `d5e0a09` 위 C02 수락 미Commit Worktree |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M2-06_progress.md`에 SEC02 단계 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-06_canary_bridge_attempt-1.md` |

원 R1-M2-06·C01·C02·SEC01 작업지시서, Attempt 3, SEC01 BLOCKED 보고, 승인 설계서·작업계획·테스트 계획을 EOF까지 읽는다. 격리 Canary 가능성 검증 결과를 재확인하고 C02 독립 검토 `ACCEPT` 상태를 보존한다.

## 2. 승인 결정과 운영 경계

- `apps/web/package.json`의 직접 Dependency `next`만 `16.2.10`에서 `16.3.0-canary.93` exact로 변경한다.
- Root Override는 사용하지 않는다.
- 이 버전은 Release 1 개발·검증용 임시 보안 브리지다.
- 안전한 PostCSS·Sharp 범위를 포함한 안정판 Next가 나오면 동일 회귀·Gate를 거쳐 즉시 안정판으로 교체한다.
- 안정판 교체 전 실제 운영 Release를 금지한다. ysna-server는 격리 테스트 환경이므로 검증 배포만 허용한다.
- Canary 운영 배포, 다른 Canary 버전, Audit 예외 채택은 신산님의 별도 승인이 필요하다.

위 결정을 `docs/01_architecture/temporary_next_canary_security_bridge.md`에 이유, 적용 범위, 위험, 검증, 종료 조건, Owner와 금지사항으로 기록한다.

## 3. 허용 변경과 금지사항

허용:

- `apps/web/package.json`의 Next exact 버전 1건
- `package-lock.json`의 Next·`@next/*`·PostCSS·Sharp·`@img/*`와 설치에 필수적인 Dependency Closure
- 임시 브리지 결정 기록
- R1-M2-06 Evidence Manifest·Canary 검증 Evidence·Progress·결과보고

금지:

- Root Override 추가
- React·React DOM·Node·npm·TypeScript와 무관 직접 Dependency 변경
- `--force`, `--legacy-peer-deps`, `npm audit fix`, Audit 예외·Suppress·Allowlist
- Quality Gate·CI·Toolchain·테스트 완화
- 기능 코드·UI·Route·API·데이터 계약 변경
- C01/C02 PNG·Browser JSON 재작성 또는 기능 합격 주장 변경
- 보호 Dirty `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`, `violations.json` 수정·복원·Stage

Targeted Install에서 Dependency Closure 밖 Drift가 나오면 원인을 조사한다. Canary가 공식 선언한 Next Closure에 필요한 변경만 수용하고, 관련 없는 버전 Drift는 승인 없이 포함하지 않는다.

## 4. 단계와 증거

| 단계 | 작업 | 필수 증거 |
| --- | --- | --- |
| SEC02-S0 | 승인·정본·현재 Diff·단일 Writer·보호 Dirty·격리 검증 재확인 | Progress |
| SEC02-S1 | Next exact 변경과 Targeted Lock 재생성·Closure Diff 검토 | package/lock Diff |
| SEC02-S2 | 깨끗한 `npm ci`, 정상 Tree·Registry·Integrity·Runtime 확인 | Exit 0, `npm ls` 정상 |
| SEC02-S3 | Audit 전 등급 0과 전용 21·전체 98·Lint | 전부 PASS |
| SEC02-S4 | Production Build·Route·실제 Browser Runtime Smoke·공통 Gate | 전부 PASS |
| SEC02-S5 | 결정 기록·Evidence·Manifest·Progress·결과보고·Diff 대조 | HANDOFF_READY |

진행 기록에는 시각, 단계, 상태, 변경 파일, 명령과 Exit, 오류·원인·복구, 다음 작업을 남긴다. OneDrive I/O, `npm ci`, Build, Gate가 오래 걸리면 살아 있는 프로세스를 시간 제한만으로 종료하지 않고 PID·CPU·출력 갱신을 확인하며 기다린다.

## 5. Dependency·보안 검증

1. `npm ls next sharp postcss --all` Exit 0이며 `invalid`, `extraneous`, `deduped invalid`가 없어야 한다.
2. Exact Tree는 `next@16.3.0-canary.93`, `postcss@8.5.10`, `sharp@0.35.3`이어야 한다.
3. 취약한 `postcss@8.4.31`, `sharp@0.34.5` 설치가 0건이어야 한다.
4. `npm audit --omit=dev --json`은 Info/Low/Moderate/High/Critical 전부 0, Exit 0이어야 한다.
5. Lockfile Registry Host는 공식 npm Registry 경계만 유지하고 Integrity 누락이 없어야 한다.
6. Sharp Runtime이 0.35.3, libvips 8.18.3 이상으로 로드되어야 한다.
7. Secret·Credential·내부 Host·감사 예외가 Package·Lock·로그·Evidence에 추가되지 않아야 한다.

## 6. 기능·Runtime 회귀

- R1-M2-06 전용 21/21
- Foundation·Workspace·Source·Run·Studio·Account 선택 회귀 98/98
- Workspace Lint 11 files
- Production Build와 Account·Organization 정적 Route, Workspace 동적 Route
- 공통 Quality Gate 전체 Category PASS
- 실제 Production Runtime에서 `/settings/account`, `/settings/organization`, 기존 Workspace Route HTTP/DOM Smoke
- Browser Console warning/error 0, same-origin 경계 유지, API 내부 주소·localhost 직접 호출 0
- C02 역할·Persona·Grant 주입 방어와 정상 관리자 Preview 회귀

UI 변경이 없으므로 기존 C01/C02 PNG는 다시 만들지 않는다. Runtime Smoke 결과는 별도 JSON Evidence로 남기고 기존 PNG Hash가 그대로인지 대조한다.

## 7. 완료와 후속 Gate

어울2 로컬 완료 후 어울1이 다음을 수행한다.

1. 최신 Diff 읽기 전용 독립 검토
2. 정확한 허용 파일만 Commit·Push
3. GitHub Required Check 확인
4. ysna-server `/home/ubuntu/deploy/daon-user/R1-M2-06/<exact-push-sha>` 격리 경로에서 ARM64 `npm ci`·Tree·Audit·21/98·Lint·Build·공통 Gate·Runtime Smoke
5. 기존 `shared-db`, `common`, `netdata`, `proxy` 무변경과 임시 자원 0 확인
6. 최종 Evidence Commit·PR·Merge

Schema·Migration 신호가 없으면 `NOT_APPLICABLE_NO_SCHEMA`, DB 명령 0건을 기록한다. 이 검증은 운영 배포가 아니다.

## 8. 완료 조건

- Next exact Canary 1건 외 직접 Dependency 변경 0
- 정상 Tree, Audit 전 등급 0, Registry·Integrity·Sharp Runtime PASS
- 전용 21/21·전체 98/98·Lint·Build·Browser Smoke·공통 Gate PASS
- 기존 기능 코드·C01/C02 Evidence Hash·same-origin 경계 회귀 0
- 임시 브리지 결정 기록에 안정판 전환·운영 금지·Owner 명시
- 보호 Dirty 2개와 범위 밖 파일 무변경
- 결과보고와 Evidence Manifest 정합

## 9. 결과보고 계약

첫 줄:

```text
status | R1-M2-06-DEP-002 | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음 판단
```

Commit·Push·ysna-server·PR·Merge는 어울2가 수행하지 않는다. 완료 조건 하나라도 빠지면 `COMPLETED`를 사용하지 않는다.

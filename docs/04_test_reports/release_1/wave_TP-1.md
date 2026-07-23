# Release 1 테스트 웨이브 TP-1 보고서

## 보고 정보

| 항목 | 값 |
| --- | --- |
| 웨이브 | TP-1 화면 적합성 · M2 Exit → G2-UX |
| 보고일 | 2026-07-23 |
| 검토 | 어울1 직접 검증 + 독립 Read-only 재검토 |
| 승인 | 신산님 · `APR-G2-UX-20260723-01` |
| 제품 검증 Commit | `a408cb903a4e756db11d966e055af9d44dc1189a` |
| 서버 증거 Commit | `02f0252` |
| PR | `#14` · `codex/r1-m2-08` → `codex/release-1` · Merge 대기 |
| 판정 | `PASS WITH OBSERVATIONS` · G2-UX `GO` 승인 |

## 기준 문서와 Hash

| 문서 | SHA-256 |
| --- | --- |
| Release 1 작업계획 | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| Release 1 테스트 계획 | `359404A190D248E94F2BE4A69CB285D10422FA426C32D4C5409F868F4CA4768B` |
| M2→M3 승계 계약 | `AB240A58F640268900ADFCB7EA47862F907E489D1E92B0F442D16F29F3736E77` |

## TP-1 판정

| 검증 항목 | 결과 | 근거 |
| --- | --- | --- |
| R1 필수 여정 8종 연결 | PASS | Workspace·지식 권위·모델 계보·Studio 생성·검토/전달/등록·계정/보안·운영/복구·부정 상태를 Evidence Hub에서 연결 |
| 미구현 성공 위장 방지 | PASS | API·DB·LLM·File·Delivery·Native Runtime은 실제 성공으로 표시하지 않고 Mock/Contract Projection/Unavailable로 구분 |
| 반응형·상태 보존 | PASS | 실제 Chrome 1920/1200/800/500, 가로 Overflow 0, Route·선택·Check 상태 보존 |
| 키보드·설명 접근성 | PASS | Focus·Tab 순환·Escape·Tooltip ARIA 계약과 기계 검증 통과 |
| 오류·권한·축소 운영·복구 | PASS | Error·Forbidden·Unavailable 화면, 권한·정책 잠금·복구 흐름 존재 |
| 생성 설정과 잠금 | PASS | 목적·독자·Source·RuleSet·분량·출력 형식·검토 조건 확인, 강제 RuleSet과 조직 검토 조건 잠금 |
| M3 재사용 계약 | PASS | IA·Route·Token·상태·접근성 Component·Layout 승계 항목과 교체 Adapter를 명시 |
| Browser 경계 | PASS | Console warning/error 0, same-origin Asset 9, non-same-origin/API-like 요청 0, 내부주소 직접 호출 0 |
| 독립 검토 | PASS | Legacy 우회 8/8 차단, CRLF 표현 정확 5건, Manifest 23/23, Reconciliation 90·82/4/4/0 |
| 로컬 전체 품질 | PASS | 전용 19/19, 전체 186/186, Lint 11, Web Build 7 routes, Quality Gate 7범주 |
| GitHub Required Check | PASS | Run `29968754368`, Job `89085864046` |
| ysna-server exact SHA | PASS | npm ci/ls/audit, 전체 186/186, Lint, Build, Gate; Checkout clean; Docker 자원 전후 Hash 동일 |
| DB Migration | N/A | R1-M2-08에는 Schema/Migration 파일 0건이며 Backend/DB 실구현은 이후 Milestone 범위 |

## Observation과 다음 단계 조건

1. 선행 Manifest 90건 중 Legacy Manifest Drift 4건은 승인된 Observation이다. 설명되지 않은 불일치는 0건이며 완료 PASS로 위장하지 않았다.
2. Web은 실제 Next Production Browser로 검증했다. Windows·Android·iOS는 M2 범위대로 Contract Projection이며 Native 실행 증거는 M3에서 획득해야 한다.
3. 실제 API·DB·LLM·파일 처리·Export·Delivery는 아직 Mock Adapter 뒤에 있다. M3가 승인 UX Shell을 승계하고 M4~M9에서 실제 Adapter로 교체해야 한다.
4. `R1-D022`의 Next `16.3.0-canary.93`은 개발·GitHub Check·ysna-server 격리 검증 전용이다. 안전한 안정판 전환 전 운영 Release는 금지한다.
5. Browser Resource Timing은 수집 불가로 0으로 추정하지 않았다. Network의 same-origin/외부/API-like 요청은 별도로 확인했다.

## 기술 의견

M2의 목적은 실제 Backend나 Native 실행을 미리 완성하는 것이 아니라 전체 사용자·운영 흐름, 부정 상태, 반응형 상태 정본과 M3 승계 경계를 고정하는 것이다. 이 목적은 충족됐다. C2/C3 잔여 결함은 없고 Observation은 다음 Milestone의 명시 계약 안에 있으므로 어울1은 `G2-UX GO`와 PR #14 병합을 권고한다.

신산님이 `2026-07-23` G2-UX GO와 PR #14 병합을 승인했다. PR 병합을 완료한 뒤에만 M3 작업지시를 발행한다.

# R1-M2-06-SEC01 보안 작업지시서 — Sharp·PostCSS 의존성 보정

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| 연계 Work Order | `R1-M2-06` |
| issue_id | `R1-M2-06-DEP-001` |
| 승인 | 신산님 승인 · 2026-07-22 |
| 목적 | 기존 기능을 유지하며 Production Dependency Audit의 Sharp·PostCSS 취약점을 안전 버전으로 제거 |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| 기준 Branch/HEAD | `codex/r1-m2-06` · `d5e0a09` 위 C02 수락 미Commit Worktree |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M2-06_progress.md`에 SEC01 단계 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-06_dependency-remediation_attempt-1.md` |

원 `R1-M2-06` 작업지시서, C01·C02 수정 작업지시서, Attempt 3 보고서, 승인 설계서·작업계획·테스트 계획을 EOF까지 읽는다. C02 기능 변경과 독립 검토 `ACCEPT` 상태를 보존한다.

## 2. 확인된 보안 기준선

- 현재 설치: `next@16.2.10` → `sharp@0.34.5`, `postcss@8.4.31`
- `sharp <0.35.0`: High · `GHSA-f88m-g3jw-g9cj`; 패치 버전 `0.35.0` 이상
- `postcss <8.5.10`: Moderate · `GHSA-qx2v-qp2m-jg93`; 패치 버전 `8.5.10` 이상
- 최신 확인 Next `16.2.11`도 `postcss@8.4.31`을 고정하고 `sharp ^0.34.5`만 허용하므로 단순 Next Patch 변경으로 두 항목을 해소할 수 없다.

승인된 최소 보정은 루트 `package.json`의 npm `overrides`로 다음 두 버전을 정확히 고정하는 것이다.

```json
"overrides": {
  "sharp": "0.35.3",
  "postcss": "8.5.21"
}
```

## 3. 구현 범위와 금지사항

허용 변경:

- 루트 `package.json`의 위 두 `overrides`
- `package-lock.json`의 해당 해석·무결성·플랫폼 패키지 변경
- R1-M2-06 Architecture 계약의 보안 기준선 기록 최소 보완
- R1-M2-06 Evidence Manifest·Progress·SEC01 결과보고

금지:

- Next·React·npm·Node 또는 다른 직접/전이 의존성의 임의 버전 변경
- `npm audit fix --force`, `--force`, `--legacy-peer-deps`, Audit 예외·Suppress·Allowlist
- Quality Gate·CI·Toolchain·테스트의 완화 또는 삭제
- 애플리케이션 기능 코드·UI·Route·API·데이터 계약 수정
- C01/C02 수락 증거와 PNG 재작성
- 보호 Dirty `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`, `violations.json` 수정·복원·Stage

Lockfile에서 승인한 두 패키지의 안전 버전을 설치하기 위해 필수적인 `@img/*`, `nanoid`, `picocolors`, `source-map-js` 등 종속 하위 노드 변경은 허용하되, 변경 이유와 Before/After를 보고한다. 그 밖의 패키지 Drift가 발생하면 원인을 조사하고 승인 없이 수용하지 않는다.

## 4. 단계별 작업

| 단계 | 작업 | 필수 증거 |
| --- | --- | --- |
| SEC01-S0 | 정본·승인·현재 Diff·단일 Writer·보호 Dirty·설치 버전 확인 | Progress, `npm ls` Before |
| SEC01-S1 | 루트 overrides만 적용하고 Lockfile을 재생성 | package/lock Diff, 승인 외 Drift 목록 |
| SEC01-S2 | 깨끗한 `npm ci`와 Dependency Tree·Integrity 확인 | Exit 0, exact version·중복 취약 버전 0 |
| SEC01-S3 | `npm audit --omit=dev --json`과 공통 Gate 보안 범주 확인 | High/Moderate/Critical 0, Audit Exit 0 |
| SEC01-S4 | 전용 21·전체 선택 98·Lint·Production Build·공통 Gate | 전부 PASS |
| SEC01-S5 | Architecture·Manifest·Progress·Attempt 보고·Diff 대조 | HANDOFF_READY 또는 정식 상태 |

각 단계 착수·완료·오류·복구·종료 직전에 지정 Progress 파일에 시각, 단계, 상태, 변경 파일, 명령과 Exit, 오류 원인, 복구, 다음 작업을 기록한다. OneDrive I/O나 Build가 오래 걸리면 살아 있는 프로세스를 시간 제한만으로 종료하지 않고 생존·CPU·출력 갱신을 확인하며 기다린다.

## 5. 공급망·호환성 검증

1. `package-lock.json`의 Registry는 기존 공식 npm Registry 경계만 유지한다.
2. `sharp@0.35.3`, `postcss@8.5.21`과 플랫폼별 Sharp 패키지의 Integrity가 Lockfile에 있어야 한다.
3. `npm ls next sharp postcss --all`에서 취약한 `sharp@0.34.5`, `postcss@8.4.31`이 남지 않아야 한다.
4. `npm audit --omit=dev --json` 결과는 Info/Low/Moderate/High/Critical 전부 0이어야 한다.
5. Next Production Build와 Account/Organization/Workspace Route 생성이 유지되어야 한다.
6. Sharp 로딩과 설치 버전을 실제 Runtime에서 확인하되 비신뢰 이미지 파일을 새로 반입하거나 외부 데이터를 처리하지 않는다.
7. Windows 로컬 통과 후 어울1이 Commit·Push하고, ysna-server의 격리 경로에서 Exact SHA·ARM64 `npm ci`·Audit·Build·공통 Gate를 재검증한다.

## 6. 완료 조건

- 승인된 두 overrides 외 직접 Dependency 변경 0건
- 취약 버전 `sharp@0.34.5`, `postcss@8.4.31` 설치 0건
- `sharp@0.35.3`, `postcss@8.5.21` exact 해석 및 Lockfile Integrity 확인
- Production Dependency Audit 취약점 전체 0, Exit 0
- 전용 21/21·전체 98/98·Lint·Production Build·공통 Gate PASS
- 기존 C02 권한 방어·Route·Browser Evidence와 기능 코드 Diff 0
- 보호 Dirty 2개와 범위 밖 파일 무변경
- 공급망·호환성·남은 위험을 결과보고에 분리 기록

## 7. 결과보고 계약

첫 줄:

```text
status | R1-M2-06-DEP-001 | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음 판단
```

Commit·Push·ysna-server·PR·Merge는 수행하지 않는다. 승인 버전이 설치·Build·Audit를 통과하지 않으면 다른 버전이나 Audit 예외로 우회하지 말고 원인·대안·현재 Diff를 포함해 `FAILURE_REPORT` 또는 `BLOCKED`로 보고한다.

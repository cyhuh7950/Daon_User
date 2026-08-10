# R1-M5-EVIDENCE-RETRO-01 완료보고

`COMPLETED | R1-M5-EVIDENCE-RETRO-01-I001 | R1-M5-01~07의 기존 Work Order·보정 이력·진행/완료보고·Evidence Pack·Git Commit을 소급 대조하고 증거 유형·파일별 기록 Commit·한계를 정규화 | Work Order별 Manifest 7개, 통합 Evidence Index, 본 Work Order Manifest·진행기록·완료보고 | JSON parse 8/8, Manifest 참조 경로·SHA-256 28/28, recorded_commit 경로·Commit object 28/28, Secret 고위험 패턴 0, git diff --check PASS; 제품 테스트·Build·서버·Browser는 범위상 미실행 | R1-M5-07 actual Web/Windows·same-origin Browser Network는 unverified이고 M5 Exit는 부분/미확보; Baseline 최초 승인 Hash와 현재 개정본 Hash 차이 유지 | 어울1이 M5 Exit 1차 검증과 R1-M5-07 Browser 증거 수집 경로를 별도 판단`

## 판정

`COMPLETED` — 이 판정은 C0 소급 증거 감사의 산출물과 정적 검증이 완료됐다는 뜻이다. R1-M5-01~07 또는 M5 Milestone Exit의 제품 완료 판정은 아니다.

## 판단 이유

- 승인 정본을 EOF까지 읽고 현재 SHA-256을 재계산했다. 설계 0.9, 구현계획 1.7, 테스트계획 0.9, Baseline Manifest는 작업지시서의 참고값과 모두 일치했다.
- `APR-CP3-PASS-GO-20260809-01`은 CP3 `PASS / GO_TO_EXPANSION`을 승인하지만, M5 Evidence Manifest와 M5~M7 Exit은 검증부채로 남긴다고 명시한다.
- R1-M5-01~07의 정규 `manifest.json`이 모두 존재하도록 보완했고, 각 Manifest의 소급 metadata에는 실존 Evidence 파일의 경로·SHA-256·유형, 환경, Commit/보정 관계, known limit를 남겼다.
- Reviewer 1차 보완으로 추적 Evidence 27개에 파일별 실제 `recorded_commit`을 추가했고, 최종 provenance 고정으로 통합 Index도 Commit `04503737cc5dbe19e74dded6f03814813dbc4028`에 연결했다. 28개 값 모두 해당 경로의 `git log -1` 결과와 일치하고 Commit object가 존재한다.
- M5-07의 원 완료보고 `VERIFYING`, 후속 기록의 `BLOCKED`, 기존 Manifest `EXTERNAL_DATA_PASS_BROWSER_EVIDENCE_PENDING`은 상충/시점별 기록으로 유지했다. 실제 Web·Windows 화면 및 same-origin Browser Network가 없으므로 `COMPLETED`로 승격하지 않았다.

## 생성·변경 결과

- 신규 정규 Manifest:
  - `docs/03_evidence/release_1/R1-M5-01/manifest.json`
  - `docs/03_evidence/release_1/R1-M5-02/manifest.json`
  - `docs/03_evidence/release_1/R1-M5-03/manifest.json`
- 기존 Manifest 최소 보완(과거 상태·명령 변경 없음):
  - `docs/03_evidence/release_1/R1-M5-04/manifest.json`
  - `docs/03_evidence/release_1/R1-M5-05/manifest.json`
  - `docs/03_evidence/release_1/R1-M5-06/manifest.json`
  - `docs/03_evidence/release_1/R1-M5-07/manifest.json`
- 통합 산출물:
  - `docs/03_evidence/release_1/R1-M5-EVIDENCE-RETRO-01/evidence-index.md`
  - `docs/03_evidence/release_1/R1-M5-EVIDENCE-RETRO-01/manifest.json`
  - `docs/04_test_reports/release_1/R1-M5-EVIDENCE-RETRO-01_progress.md`
  - 이 완료보고

## 테스트 결과

| 검증 | 결과 |
| --- | --- |
| 생성·보완 Manifest JSON parse | 8/8 PASS |
| Manifest가 참조한 기존 Evidence 파일 존재·SHA-256 재계산 | 28/28 PASS |
| 파일별 `recorded_commit`과 해당 경로의 최신 Git 기록 Commit·Commit object | 28/28 PASS |
| 생성·변경 문서 Secret/Credential 고위험 패턴 | 0건 PASS |
| `git diff --check` | PASS (줄끝 변환 경고만 존재) |
| 제품 테스트·Build·서버·DB/Object/API·Browser/Network 재실행 | 미실행 — 작업지시서 제외 범위 |

## 미해결 사항

1. M5 Exit는 `부분` 또는 `미확보`다. 특히 Backup/Restore·Local 손상 복구의 actual Web/Windows 화면 및 Browser same-origin Network 증거가 없다.
2. R1-M5-07은 Browser 증거 확보 전 `VERIFYING`이며, 제품 완료 또는 M5 Exit PASS를 주장할 수 없다.
3. Baseline Manifest의 최초 승인 Hash와 현재 승인 개정본의 Working Tree Hash 차이는 정상적으로 보존됐고, 이번 범위에서 Baseline을 갱신하지 않았다.
4. 원 Work Order가 남긴 격리 자원/서버 checkout은 별도 파괴 작업 승인 없이는 정리 대상이 아니다.

## 다음으로 필요한 판단

어울1은 통합 Index를 기준으로 M5 1차 Exit 검증을 수행하고, R1-M5-07의 운영형 Web/API 기동 및 실제 Web·Windows·same-origin Browser Network 증거 수집을 별도 승인 Work Order로 분리할지 판단해야 한다.

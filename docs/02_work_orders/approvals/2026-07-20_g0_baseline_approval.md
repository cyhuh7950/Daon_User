# G0-BASELINE 승인 기록

| 항목 | 값 |
| --- | --- |
| 승인 기록 ID | `APR-G0-BASELINE-20260720-01` |
| Gate | `G0-BASELINE` |
| 승인자 | 신산님 |
| 승인일 | 2026-07-20 |
| 승인 상태 | `APPROVED` |
| 기준 보고서 | `docs/04_test_reports/release_1/gate_G0-BASELINE_candidate.md` |
| 기준 Manifest | `docs/02_work_orders/release_1_baseline_manifest.json` |

## 승인된 결정

- `R1-D001`: Windows 11 23H2 이상, Chrome·Edge 최신 2개 주 버전, Android 12 이상, iOS 17 이상.
- `R1-D003`: Hybrid Pilot. 로컬/Windows Local-private, WSL 통합, OCI Seoul Managed Cloud 운영 경로. On-prem 정식 배포는 R1 Pilot 제외.
- `R1-D009`: 삭제 유예 30일, Audit 1년, RPO 15분, RTO 4시간. Legal Hold 우선.
- `R1-D010`: 파일당 100MB, Workspace당 20GB, 사용자 동시 Run 2개, 조직 동시 Run 20개, 상호작용 API p95 3초 이내(비동기 작업 제외).

## 조건부 진행

`R1-D004`, `R1-D006`, `R1-D007`, `R1-D008`, `R1-D011`, `R1-D012`는 승인으로 해소된 것이 아니다. 외부 환경·계정·장치·계약이 필요한 상태를 유지하며, 연결된 Work Order만 준비 전까지 `BLOCKED`로 처리한다.

## 승인 효과

- Release 1 전체 구현 상태를 `BLOCKED`에서 `READY`로 전환한다.
- 첫 개발 Work Order는 `R1-M1-01`이다.
- 파괴적 작업, 외부 배포, 예외 수용과 Release 최종 완료는 별도 승인을 유지한다.

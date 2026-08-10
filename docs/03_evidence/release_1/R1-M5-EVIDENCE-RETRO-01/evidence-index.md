# R1-M5 Evidence Index — 소급 감사

## 감사 경계

- 감사 Work Order: `R1-M5-EVIDENCE-RETRO-01` (`R1-M5-EVIDENCE-RETRO-01-I001`)
- 기준: 설계 0.9, 구현계획 1.7 §15, 테스트계획 0.9 M5, Baseline Manifest, `APR-CP3-PASS-GO-20260809-01`, 검증부채표.
- 방법: 기존 Work Order·보정 이력·진행/완료보고·Evidence Pack·Git Commit을 읽기 전용으로 대조했다. 새 Runtime, DB/Object/API, Browser/Network 증거는 수집하지 않았다.
- 분류: `contract/static`, `automated_test`, `actual_db_object_api`, `actual_ui_network`, `historical_record`, `unverified`.
- 본 문서는 M5 또는 개별 제품 Work Order의 최종 완료 판정이 아니다. 과거 `COMPLETED`는 원 기록의 상태를 보존해 표시한다.
- 각 Manifest의 `evidence_files`는 해당 파일을 마지막으로 기록한 Git Commit을 `recorded_commit`으로 보존한다. 이 Index 자체는 현재 미커밋 산출물이므로 Retro Manifest에서 `recorded_commit: null`, `provenance_status: pending_current_commit`으로 명시한다.

## Work Order별 정규 Manifest

| Work Order | 원 기록 상태 | 소급 감사 판정 | 주 Commit | 실제 증거 | 알려진 한계 |
| --- | --- | --- | --- | --- | --- |
| R1-M5-01 | `COMPLETED` + C01 `COMPLETED` | `historical_record_completed` | `6d8d079`, C01 `f872e89` | ysna-server PostgreSQL 18.4/pgvector, Migration/Restore, RLS, API | Browser/UI/Network, OCI 미실행 |
| R1-M5-02 | `COMPLETED` + C01 `COMPLETED` | `historical_record_completed` | `f3da3c7` | ysna-server PostgreSQL 18.4/MinIO, Object 16/16, Runtime 15/15 | Browser Network/TP/OCI 미실행 |
| R1-M5-03 | `COMPLETED`; C01 `INCOMPLETE`, C02 `COMPLETED` | `historical_record_completed_with_correction` | C01 `c21a6b6`, C02 `11a121e` | Windows installed NSIS/runtime, Ubuntu ARM64, GitHub CI | C01 INCOMPLETE 1회; 서버 checkout 보존 |
| R1-M5-04 | `COMPLETED` | `historical_record_completed` | C02 `a6752c5` | ysna-server PostgreSQL 18.4, Migration 0003, RLS/lineage/API health | S3 2건 범위 밖, Browser Network 미주장 |
| R1-M5-05 | `COMPLETED` | `historical_record_completed` | `365015a` | PostgreSQL/MinIO, RLS, Sync API/Worker, Local encrypted restart | Browser Network/TP/OCI/Cleanup 미실행 |
| R1-M5-06 | `COMPLETED` | `historical_record_completed` | `0f3b1c1` | PostgreSQL/MinIO, Retention API, RLS, Local tombstone | 격리 자원 보존; Node/Next 1건은 ephemeral harness |
| R1-M5-07 | `VERIFYING`; Manifest `EXTERNAL_DATA_PASS_BROWSER_EVIDENCE_PENDING` | `historical_record_verifying` | `bc139fa` | PostgreSQL/MinIO 3 integration, Local/Cloud HTTP, Build/Quality | Web·Windows actual 화면 및 same-origin Browser Network `unverified` |

보정 Work Order는 원 Work Order Manifest의 `correction_relationships`로 연결했다. 원 기록, 명령, 상태를 덮어쓰지 않았다. 각 Manifest의 `evidence_files`에만 존재 경로와 SHA-256을 기록했으며, 자기 자신은 재귀 checksum 변동을 방지해 검증기에서 별도 parse·존재 확인한다.

## 구현계획 §15 필수 완료 증거 매핑

| Work Order | §15 필수 완료 증거 | 근거 분류·경로 | 소급 상태 |
| --- | --- | --- | --- |
| R1-M5-01 | Migration 재적용, Transaction, Tenant 격리, 지원 major 경계 | `actual_db_object_api`: `R1-M5-01/server-validation-manifest.json`, C01 `server-verification.json` | `PASS` (과거 서버 기록) |
| R1-M5-02 | 원본·산출물 Digest, 실패 Queue·재처리 | `actual_db_object_api`: `R1-M5-02-C01/server-validation-summary.md`; 검증 script는 `contract/static` | `PASS` (과거 서버 기록) |
| R1-M5-03 | Restart, 암호화, Vector 검색, Key 철회 | `actual_ui_network`: installed app/runtime JSON; `automated_test`: C01/C02 verification | `PASS` (과거 Windows/CI 기록) |
| R1-M5-04 | Migration, FK, 상태 전이, Snapshot 불변 | `actual_db_object_api`: `R1-M5-04/server-validation-summary.md`; validator는 `contract/static` | `PASS` (과거 서버 기록) |
| R1-M5-05 | 무승인 전송 0, 원본 암묵 변경 0, 자동 병합/덮어쓰기 0, Batch 재개/Audit | `historical_record`: `R1-M5-05/completion-report.md`; 기존 Manifest의 server/local fields | `PASS_WITH_TESTED_SCOPE`; Browser Network는 미실행 |
| R1-M5-06 | TDD 부정 경로, Migration 0005, RLS/6 Route, 재시도/Local Ack/Audit/Fixture | `actual_db_object_api`: `R1-M5-06/verification-summary.md` | `PASS` (과거 서버 기록) |
| R1-M5-07 | TDD 부정 경로, Migration 0006, RLS/MinIO, 7+3 Route, Retention 우선, Local repair, 화면/Network, G9-DRILL Fail-close | `actual_db_object_api`: `R1-M5-07/verification-summary.md`; `contract/static`: OpenAPI JSON; 화면/Network는 `unverified` | `VERIFYING` |

## M5 Exit Gate 소급 대조

| M5 Exit 항목 | 판정 | 근거 | 소급 한계 |
| --- | --- | --- | --- |
| Local-private와 Cloud-sync 경로를 별도 테스트로 통과 | `부분` | M5-01/02 Cloud, M5-03 Local, M5-05 Sync의 개별 기록 | M5 Exit 통합 재실행 기록은 없음 |
| 저장·전송 암호화, Tenant·Workspace·영역별 Key 분리 | `부분` | M5-01 RLS, M5-03 SQLCipher/credential, M5-05 encrypted queue 기록 | 전체 Exit 차원의 key-separation 통합 증거는 없음 |
| 승인 없는 영역 이동 및 External 전송 0 | `부분` | M5-05 무승인 전송/Offline network 0의 과거 서버·자동 기록 | Browser Network 캡처·실제 여정은 없음 |
| Backup/Restore와 Local 손상 복구를 Web·Windows 화면/API에서 확인, Cloud 호출 same-origin 증명 | `미확보` | M5-07 API·data integration·Build는 존재 | M5-07 actual Web/Windows UI와 Browser Network가 명시적으로 pending |
| Preview/Execute 현재 권한·정책·Step-up, Allowlist 밖 Restore 0, Purged 부활 0 | `부분` | M5-07 PostgreSQL/MinIO/API 자동·서버 증거 | Browser/운영형 여정 미확보; G9-DRILL은 범위 밖 |
| G9-DRILL 전 운영 Restore·파괴적 손상 주입 Fail-close | `부분` | M5-07 fixture-only/운영 대상 미실행 기록 | 실제 G9-DRILL은 미실행; 제품 Exit PASS를 주장하지 않음 |

## 상충·미확보·검증부채

1. Baseline Manifest에는 최초 승인 Git Blob Hash가 남아 있고, 이번 Working Tree의 설계·계획·테스트계획 Hash는 승인 후 개정으로 다르다. 본 감사는 Baseline을 수정하지 않는다.
2. `R1-M5-07_completion_report.md`에는 `VERIFYING`과 후속 외부 검증 재개 기록의 `BLOCKED`가 함께 있다. 현재 Manifest의 `EXTERNAL_DATA_PASS_BROWSER_EVIDENCE_PENDING`을 유지하며 어느 쪽도 `COMPLETED`로 승격하지 않는다.
3. CP3 `PASS / GO_TO_EXPANSION`은 M5 Evidence Manifest나 M5 Exit 완료를 의미하지 않는다. 검증부채표의 M5 Evidence Manifest·M5~M7 Exit은 본 소급 감사 후에도 어울1의 별도 검증 대상이다.
4. 제품 테스트, Build, 서버, Browser 검증은 이번 Work Order에서 재실행하지 않았다.

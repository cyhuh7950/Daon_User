# R1-M5 Evidence Manifest 소급 정합화 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-EVIDENCE-RETRO-01` |
| issue_id | `R1-M5-EVIDENCE-RETRO-01-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 변경 등급 | `C0` 증거·검증 기록 복구 |
| 설계·기술 책임자 | 어울1 |
| 개발 수행자 | 어울2 · `daon-developer` |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 시작 기준 HEAD | `e74ee123324b500d087cc919958bf7382ae1bf3e` |
| 진행 복구 기록 | `docs/04_test_reports/release_1/R1-M5-EVIDENCE-RETRO-01_progress.md` |
| 결과보고 | `docs/04_test_reports/release_1/R1-M5-EVIDENCE-RETRO-01_completion_report.md` |

## 2. 승인 기준 문서

다음 문서를 요약본으로 대체하지 말고 EOF까지 읽은 뒤 적용 조항과 현재 Hash를 진행 기록에 남긴다.

- `AGENTS.md`
- 상세 설계 정본 `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` · 버전 `0.9`
- Release 1 구현계획 `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` · 버전 `1.7`
- Release 1 테스트계획 `docs/04_test_reports/release_1_test_plan.md` · 버전 `0.9`
- 승인 기준선 `docs/02_work_orders/release_1_baseline_manifest.json`
- CP3 최종 승인 `docs/02_work_orders/approvals/APR-CP3-PASS-GO-20260809-01.md`
- 검증부채 `docs/04_test_reports/release_1/verification_debt_2026-08-04.md`
- R1-M5-01~07 작업지시서·보정 작업지시서·진행기록·완료보고·기존 Evidence Pack 전체

현재 Working Tree 파일 SHA-256 참고값은 설계 `87F338381F62ECD36EE5991969950BC87F154F0E229C65322B5BE0162C224560`, 계획 `F63DD247D35FBB9E45A5D724F907AEC25C9EB00A0B5FD12A94F519972D7E4096`, 테스트계획 `CF607EE9CF25552F051BBC382EB269E5AE11C7C0E614C7C6D2223FE6DE7560F2`, Baseline Manifest `530D6FB9C9EDBCB41A8D6DA1CD61046367176CD6978D0A1B2D9CF04BFDCFCCED`다. 직접 다시 계산해 일치 여부를 기록한다. Baseline Manifest의 최초 승인 Hash와 현재 승인 개정본 Hash가 다른 사실을 숨기거나 임의로 Baseline을 갱신하지 않는다.

## 3. 목표와 완료 조건

R1-M5-01~07의 이미 존재하는 작업보고·Git 이력·Evidence Pack을 소급 감사하여 Work Order별 정규 `manifest.json`과 통합 Evidence Index를 만든다. 새로운 실행 증거를 꾸며내지 않고 정적·계약·자동 테스트·실제 DB/Object/API·화면/Network 증거를 분리한다.

완료 조건:

1. R1-M5-01~07 각각에 `docs/03_evidence/release_1/<WO>/manifest.json`이 존재한다.
2. 각 Manifest가 실제 존재하는 파일만 참조하며 파일별 SHA-256, 증거 생성/기록 Commit, 환경, 증거 유형, 알려진 한계를 기록한다.
3. 보정 Work Order 증거는 원 Work Order Manifest에서 별도 관계로 연결하며 원본 이력을 덮어쓰지 않는다.
4. `R1-M5-01~07` 통합 Evidence Index가 계획 §15의 필수 완료 증거와 M5 Exit 항목을 Work Order별로 매핑한다.
5. `PASS`, `VERIFYING`, `BLOCKED`, `DEFERRED`, `미실행`을 실제 증거대로 구분한다. 테스트 통과를 실제 화면·운영 검증 통과로 승격하지 않는다.
6. R1-M5-07 완료보고 안의 상충 상태와 실제 Web/Windows·same-origin Network 미확보 여부를 수정하지 말고 검증 발견사항으로 명시한다.
7. 이 증거 복구 Work Order 자체의 Manifest, 진행 기록, 완료보고가 생성되고 JSON parse·경로 존재·checksum 재계산·`git diff --check`가 통과한다.

이 Work Order의 `COMPLETED`는 증거 소급 감사 작업의 완료만 뜻한다. R1-M5-01~07 또는 M5 Exit의 제품 완료 판정이 아니다.

## 4. 포함 범위

- R1-M5-01~07 작업지시서·진행기록·완료보고·기존 Evidence Pack·관련 Git Commit의 읽기 전용 대조
- 누락된 원 Work Order 정규 `manifest.json` 생성
- 기존 원 Work Order `manifest.json`의 경로·checksum·분류 누락 보완
- `docs/03_evidence/release_1/R1-M5-EVIDENCE-RETRO-01/evidence-index.md` 생성
- `docs/03_evidence/release_1/R1-M5-EVIDENCE-RETRO-01/manifest.json` 생성
- 진행 복구 기록과 완료보고 생성

## 5. 제외 범위

- 제품 코드·테스트 코드·Migration·OpenAPI·설계서·구현계획·테스트계획·결정 기록 변경
- 기존 완료보고와 과거 진행기록의 문구 수정 또는 역사 재작성
- 기존 Evidence 파일 삭제·이동·내용 재생성
- DB·Object Storage·서버·Docker·Browser·WSL·ysna-server 실행 또는 변경
- 과거 테스트 재실행, 새 Runtime·화면·Network 증거 수집
- R1-M5 개별 Work Order와 M5 Milestone의 최종 완료 판정
- Commit·Push·배포

## 6. 허용 변경 경로

- `docs/03_evidence/release_1/R1-M5-01/manifest.json`
- `docs/03_evidence/release_1/R1-M5-02/manifest.json`
- `docs/03_evidence/release_1/R1-M5-03/manifest.json`
- `docs/03_evidence/release_1/R1-M5-04/manifest.json`
- `docs/03_evidence/release_1/R1-M5-05/manifest.json`
- `docs/03_evidence/release_1/R1-M5-06/manifest.json`
- `docs/03_evidence/release_1/R1-M5-07/manifest.json`
- `docs/03_evidence/release_1/R1-M5-EVIDENCE-RETRO-01/**`
- `docs/04_test_reports/release_1/R1-M5-EVIDENCE-RETRO-01_progress.md`
- `docs/04_test_reports/release_1/R1-M5-EVIDENCE-RETRO-01_completion_report.md`

기존 Manifest를 수정할 때는 과거 결과·상태·명령을 바꾸지 않고 소급 감사 metadata만 추가한다. 기존 구조와 안전하게 병합할 수 없으면 수정하지 말고 통합 Evidence Index에 결함으로 기록한다.

## 7. 보존 대상과 알려진 위험

- 착수 시 존재하는 제품 파일 삭제 표시 33건은 사용자 작업으로 간주해 Restore·Stage·수정·삭제하지 않는다.
- 다음 미추적 사용자 문서 3개를 읽기 전용으로 보존하고 Stage하지 않는다.
  - `docs/04_test_reports/release_1/interim_review_2026-07-30.md`
  - `docs/04_test_reports/release_1/interim_review_2026-08-04.md`
  - `docs/04_test_reports/release_1_model_provider_queries.md`
- R1-M5-01~03은 원 Work Order 정규 Manifest가 없거나 불완전하다.
- R1-M5-07 완료보고에는 `VERIFYING`과 과거 `BLOCKED` 기록이 함께 있어 최종 판정으로 합치지 말고 시점별 상태로 분리해야 한다.
- Baseline Manifest의 최초 승인 Hash와 현재 승인 개정본 Hash가 다르다. 이번 작업은 이를 숨기거나 수정하지 않고 한계로 기록한다.

## 8. 수행 절차

1. 공식 경로·`master`·origin·HEAD·Dirty 상태와 단일 Writer를 진행 기록에 남긴다.
2. §2 문서를 EOF까지 읽고 Hash·승인 상태·적용 조항을 기록한다.
3. R1-M5-01부터 07까지 순서대로 작업지시서, 보정 이력, 진행기록, 완료보고, Evidence 파일, 관련 Commit 존재를 대조한다.
4. 각 증거를 `contract/static`, `automated_test`, `actual_db_object_api`, `actual_ui_network`, `historical_record`, `unverified` 중 하나로 분류한다.
5. 누락된 원 Work Order Manifest를 만들고 기존 Manifest는 허용 범위에서 최소 보완한다.
6. 통합 Evidence Index에 계획 §15 완료 증거와 M5 Exit Gate별 `충족`, `부분`, `미확보`, `상충` 판정과 근거 경로를 기록한다.
7. 모든 JSON을 parse하고 모든 참조 경로·SHA-256을 재검증한다. Secret·Credential·개인정보 포함 여부를 검색한다.
8. 허용 범위 밖 Diff 0건과 기존 Dirty 보존을 확인하고 완료보고를 작성한다.

## 9. 필수 검증

- 모든 생성·변경 JSON parse 성공
- Manifest의 모든 로컬 파일 경로 존재
- 기록된 SHA-256 전수 재계산 일치
- 관련 Git Commit 존재 확인
- Work Order별 필수 완료 증거와 Manifest 매핑 누락 0건 또는 명시적 `unverified/known_limit`
- Secret 고위험 패턴 0건
- 허용 범위 밖 신규 Diff 0건
- `git diff --check` 통과

제품 테스트·Build·서버·브라우저 검증은 이번 범위가 아니므로 실행하지 않고 `미실행`으로 보고한다.

## 10. 진행 기록과 결과 계약

진행 기록은 착수, 문서·Hash 확인, 각 Work Order 감사 완료, 오류·복구, Manifest 생성, 검증 완료, 종료 직전에 갱신한다. 각 행은 다음 필드를 포함한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

종료 보고는 다음 한 줄 계약과 상세 근거를 포함한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

구현계획·요구사항·공개 API·데이터·보안·배포 경계의 변경이 필요하면 쓰기를 멈추고 `BLOCKED`로 어울1에게 되돌린다.

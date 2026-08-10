# R1-M5-07 Web same-origin Network 원본 보완 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WEB-NETWORK-02` |
| issue_id | `R1-M5-07-WEB-NETWORK-02-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 승인 | 신산님 2026-08-10 M5 후속 검증 진행 승인 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 기준 Commit | `6ae48531f1911af256839b4307180a834172bf8f` |
| Runtime 제품 Commit | `d0f0d0985120b78e8b6a0d32e22c69df12d3969e` |
| 대상 URL | `https://daon-user.sinsan.kr/operations` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WEB-NETWORK-02_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WEB-NETWORK-02_completion_report.md` |

## 2. 승인 기준

다음 문서를 EOF까지 읽고 Hash·승인·적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.9
- `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.7 §15·§21~24
- `docs/04_test_reports/release_1_test_plan.md` 0.9
- `docs/02_work_orders/release_1/R1-M5-07_work_order.md`
- `docs/02_work_orders/release_1/R1-M5-07-WEB-EVIDENCE-01_work_order.md`
- `docs/04_test_reports/release_1/R1-M5-07-WEB-EVIDENCE-01_completion_report.md`
- `docs/03_evidence/release_1/R1-M5-07-WEB-EVIDENCE-01/manifest.json`
- `docs/04_test_reports/release_1/M5_milestone_exit_retrospective_2026-08-10.md`
- `docs/02_work_orders/approvals/APR-CP3-PASS-GO-20260809-01.md`

## 3. 목표

로그인된 Chrome의 실제 `/operations` Recovery 여정에서 Session과 Backup 목록 요청의 URL·Method·Status를 원본에 가까운 실행 증거로 확보한다. 요청이 same-origin 상대 경로를 사용하고 Browser 실행 코드에서 `localhost`, `127.0.0.1`, Docker 내부 Host/Port, 내부 API 절대주소와 `NEXT_PUBLIC_API_BASE_URL` 직접 호출이 0건임을 확인한다.

화면 `ready` 증거는 이미 확보됐다. 이번 작업은 Network 미증명 항목만 보완하며 제품 코드·서버·DB를 변경하지 않는다.

## 4. 실행 방법과 허용 범위

1. Chrome Browser Skill과 Browser Client의 전체 문서를 먼저 읽는다.
2. 신산님이 로그인한 Chrome에서 검증용 새 Tab만 사용한다.
3. Browser Client가 정식 Network event/response 원본 API를 제공하면 이를 최우선으로 사용한다.
4. 정식 Network API가 없으면 Browser Client의 동일 Tab 실행 Context에서 `fetch`와 `XMLHttpRequest`를 읽기 전용으로 계측할 수 있다.
   - 호출 URL, Method, 응답 Status, 응답 URL, 시작·종료 시각만 메모리에 수집한다.
   - Header, Cookie, Token, Request/Response Body, Local/Session Storage는 수집하지 않는다.
   - 자동 Recovery 초기화 또는 `목록 새로고침` 1회만 관찰한다.
   - 계측은 검증용 Tab에만 임시 적용하고 검증 직후 원복 또는 Tab 종료로 제거한다.
5. 상대 URL은 현재 `location.origin`과 결합한 최종 URL도 함께 기록해 same-origin 여부를 기계적으로 판정한다.
6. Session 요청과 Backup 목록 요청을 식별하고 실제 URL·Method·Status를 기록한다.
7. 검증용 Tab을 종료하고 Browser 제어를 신산님에게 즉시 반환한다.

## 5. 제외 범위·안전 경계

- 제품·테스트 코드, Migration, OpenAPI, 설계·계획 변경
- 서버 배포, Docker·Proxy·DB·Object Storage 설정 및 데이터 변경
- Backup 생성, Restore Preview·Execute·Cancel, Purge 등 상태 변경 요청
- Cookie·Storage·Password·인증 Header·Token·본문 수집
- standalone Playwright, Computer Use, 외부 Browser 자동화 서버로 전환
- 사용자 기존 Tab 조작·종료
- Windows 소스 복원·Build·설치

공식 Browser Client로 증거를 수집할 수 없으면 다른 수단으로 우회하지 말고 `BLOCKED / NETWORK_CAPTURE_CAPABILITY_UNAVAILABLE`로 보고한다.

## 6. 완료 조건

1. Session 요청과 Backup 목록 요청 각각의 URL·Method·Status·same-origin 판정이 존재한다.
2. Browser 직접 내부주소·localhost 호출 0건이 수집된 요청 목록으로 확인된다.
3. 화면 상태·Actor/Workspace·Runtime Commit·Web/API Image·검증 시각과 Network 기록이 연결된다.
4. 계측 방식, 원복 여부, 수집하지 않은 민감정보가 명시된다.
5. 원본 JSON 또는 동등한 구조화 증거, Screenshot/DOM 연결, SHA-256 Manifest가 존재한다.
6. JSON parse, SHA 대조, Secret scan, `git diff --check`가 통과한다.
7. 검증용 Tab이 종료되고 Browser 제어가 반환된다.

## 7. 허용 변경 경로

- `docs/03_evidence/release_1/R1-M5-07-WEB-NETWORK-02/**`
- `docs/03_evidence/release_1/R1-M5-07/manifest.json`의 Network 증거 연결 metadata
- `docs/04_test_reports/release_1/R1-M5-07-WEB-NETWORK-02_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WEB-NETWORK-02_completion_report.md`

## 8. 보존 대상

- 기존 사용자 삭제 33건과 미추적 사용자 문서 3개
- 기존 Chrome Tab과 사용자 화면
- 기존 Evidence Pack과 과거 인증·Resource 오류 시도
- 운영 DB·Object·Backup·Restore 데이터

## 9. 진행 기록

착수, 정본·배포 SHA, Browser 연결, 계측 설치·요청 관찰·계측 제거, 증거 저장, Browser 종료, 검증과 종료 직전에 다음 필드를 기록한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

## 10. 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

`COMPLETED`는 Network 완료 조건이 모두 충족될 때만 사용한다. 화면 정상만으로 Network PASS를 주장하지 않는다.

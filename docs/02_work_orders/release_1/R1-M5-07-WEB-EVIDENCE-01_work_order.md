# R1-M5-07 Web 실제 화면·same-origin Network 증거 보완 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WEB-EVIDENCE-01` |
| issue_id | `R1-M5-07-WEB-EVIDENCE-01-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 성격 | R1-M5-07 미확보 실제 Web 증거 보완 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| Runtime 제품 Commit | `061bc4dcbddfd839fdcb64aa21ed498fe1e70e0b` |
| Web Image | `sha256:e056da75e9d666249a21b48ebe645908758bc24494f84e58dab7c7086b760bc4` · 생성 2026-08-09 01:33 KST |
| API Image | `sha256:91b454616ef6ee2c63ca6a02bcac59b576f0ccb1c4c6affc9830b66da38ba581` · 생성 2026-08-09 01:33 KST |
| 서버 Checkout HEAD | 실행 시 확인하며 문서 Fast-forward HEAD를 Runtime Build SHA로 사용하지 않음 |
| 대상 URL | `https://daon-user.sinsan.kr/operations` |
| 실행 Browser | 신산님이 로그인한 Chrome |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WEB-EVIDENCE-01_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WEB-EVIDENCE-01_completion_report.md` |

## 2. 승인 기준

다음 문서를 EOF까지 읽고 Hash·승인·적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.9
- `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.7 §15·§21~24
- `docs/04_test_reports/release_1_test_plan.md` 0.9
- `docs/02_work_orders/release_1/R1-M5-07_work_order.md`
- `docs/04_test_reports/release_1/R1-M5-07_completion_report.md`
- `docs/03_evidence/release_1/R1-M5-07/manifest.json`
- `docs/04_test_reports/release_1/M5_milestone_exit_retrospective_2026-08-10.md`
- `docs/02_work_orders/approvals/APR-CP3-PASS-GO-20260809-01.md`

구 R1-M5-07 작업지시서의 OneDrive 경로는 최신 `AGENTS.md`와 신산님 결정으로 대체한다. 제품 요구·보안·증거 계약은 그대로 유지한다.

## 3. 목표

현재 ysna-server 배포본의 실제 Web 운영 화면을 로그인된 Chrome에서 열고, Backup 목록·상태·안전 경고·복구 UI가 Prototype이 아닌 실제 Adapter에 연결되는지 확인한다. Browser Network에서 Cloud API 요청이 same-origin 상대 경로이고 `localhost`, `127.0.0.1`, Docker 내부 Host/Port, 내부 API 절대주소, `NEXT_PUBLIC_API_BASE_URL` 직접 호출이 0건임을 실제 요청 URL로 증명한다.

이 작업은 Web 증거만 보완한다. Windows 설치형 증거, 실제 Restore 성공, M5 Exit 최종 통과를 주장하지 않는다.

## 4. 포함 범위

- 로그인된 Chrome 기존 Session으로 `/operations` 접근
- 화면 제목·상태·Backup 목록 또는 명시적 Empty/Error 상태·Recovery API Panel 확인
- 페이지가 발생시키는 session·Backup 목록·상세 조회 등 읽기 요청의 실제 URL·Method·Status 확인
- Web Client 요청이 same-origin `/bff/...` 또는 승인된 same-origin 공개 경로인지 확인
- UI와 실제 Network 결과 연결: 확인 시각, 배포 Commit, Actor/Workspace 표시값, 요청 URL, Status, 화면 상태
- 상태가 Empty/Error/Forbidden이면 이를 실제 결과로 기록하고 성공으로 위장하지 않음
- Screenshot·Network 요약·DOM/화면 상태·검증 메타데이터 증거 생성
- 사용한 새 Tab과 DevTools/검증 화면을 종료하고 Browser 제어를 신산님에게 반환

## 5. 제외 범위·안전 경계

- 제품·테스트 코드, Migration, OpenAPI, 설계·계획, 기존 완료보고 수정
- DB·Object Storage 직접 조회·수정, SSH·Docker·서버 설정 변경
- Backup 생성, Restore Preview·Execute·Cancel 등 서버 상태를 바꾸는 클릭
- 운영 Restore, 파괴적 손상 주입, Fixture 생성·Seed·Cleanup
- Windows App Build·설치·실행·소스 복원
- Cookie·Local Storage·Password·Session 저장소 직접 열람
- 다른 Browser로 전환하거나 로그인 정보를 입력·변경
- Commit·Push·배포

읽기 전용 화면·Network 증거만 수집한다. 기존 Test Fixture가 화면에 있더라도 상태 변경 버튼은 누르지 않는다. 인증이 만료되었으면 우회하지 말고 `BLOCKED / AUTHENTICATION_REQUIRED`로 보고한다.

## 6. 완료 조건

1. 대상 URL의 실제 화면 상태가 Screenshot과 텍스트 근거로 기록된다.
2. Browser Network에서 최소 session 요청과 Backup 목록 요청의 실제 URL·Method·Status를 기록한다.
3. Browser 실행 코드의 요청 URL에서 내부주소·localhost 직접 호출 0건을 확인한다.
4. Backup 데이터가 없거나 API 오류면 Empty/Error 상태와 실제 응답을 기록하고 `PASS`로 위장하지 않는다.
5. 실제 화면과 Network를 Runtime 제품 Commit `061bc4d...`, Web/API Image ID·생성 시각, 검증 시각, Actor/Workspace 표시값에 연결한다. 서버 Checkout의 문서-only HEAD는 별도로 기록한다.
6. 사용한 Browser Tab·DevTools를 종료하고 신산님이 즉시 화면을 사용할 수 있게 한다.
7. Evidence Pack·Manifest·진행기록·완료보고의 JSON/경로/SHA·Secret scan·Diff check가 통과한다.

## 7. 허용 변경 경로

- `docs/03_evidence/release_1/R1-M5-07-WEB-EVIDENCE-01/**`
- `docs/03_evidence/release_1/R1-M5-07/manifest.json`의 소급 Web 증거 연결 metadata
- `docs/04_test_reports/release_1/R1-M5-07-WEB-EVIDENCE-01_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WEB-EVIDENCE-01_completion_report.md`

## 8. 보존 대상

- 기존 사용자 삭제 표시 33건과 미추적 사용자 문서 3개
- 기존 Chrome Tab·사용자 작업 화면. 검증용 새 Tab만 사용하고 종료한다.
- 기존 R1-M5-07 Evidence·완료보고·격리 서버 자원
- 기존 DB·Object·Backup·Restore 데이터

## 9. 진행 기록

착수, 정본·배포 SHA 확인, Browser 연결, 인증 상태, 화면 상태, 각 Network 요청 검증, 증거 저장, Browser 종료, 파일 검증, 종료 직전에 다음 필드로 기록한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

## 10. 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

이 작업의 `COMPLETED`는 읽기 전용 Web 증거 수집 완료만 뜻한다. 화면·Network 결과가 요구조건을 충족하면 `WEB_EVIDENCE_PASS`, 인증·환경 차단이면 `BLOCKED`, 기능 결함이면 실제 오류와 근거를 보고한다. M5 Exit 최종 판정은 어울1이 별도로 수행한다.

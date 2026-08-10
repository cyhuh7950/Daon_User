# M5 Milestone Exit 소급 검증 보고서

## 문서 정보

| 항목 | 내용 |
| --- | --- |
| 검증 대상 | M5 — Local·Cloud Data와 Sync |
| 검증 유형 | 어울1 1차 Milestone Exit 소급 검증 |
| 검증일 | 2026-08-10 |
| 기준 계획 | `DAON-USER-R1-PLAN` 1.7 §15 |
| 증거 감사 | `R1-M5-EVIDENCE-RETRO-01` |
| 증거 결과 Commit | `04503737cc5dbe19e74dded6f03814813dbc4028` |
| provenance 확정 Commit | `42afc20b4bb0a800e61c2363afbdea322fc21ea8` |
| 설계·기술 책임자 | 어울1 |

## 판정

`VERIFYING / NO_GO_TO_M5_EXIT`

M5 Evidence Manifest 소급 정합화 작업은 `COMPLETED`로 수락한다. 그러나 M5 제품 Exit는 필수 실제 화면·Network 증거가 미확보이므로 통과시키지 않는다. 이 판정은 TP-2·TP-5 독립 테스트 웨이브의 실행 또는 통과를 의미하지 않는다.

## 판단 이유

1. R1-M5-01~07 각각의 정규 `manifest.json`과 파일별 SHA-256·기록 Commit provenance가 존재하며, JSON `8/8`, 증거 경로·SHA `28/28`, recorded Commit `28/28`을 어울1이 재검증했다.
2. 내부 Task Review에서 최초 Important 2건을 보완했고, scoped Re-review와 post-commit provenance Review가 모두 승인됐다. 이는 내부 검토이며 CLAUDE 외부 독립 검증을 대신하지 않는다.
3. 계획 §15의 M5 Exit 항목 6개 중 Local·Cloud 분리, 암호화·Key 분리, 무승인 영역 이동 0건, Preview/Execute 보안, G9-DRILL 전 Fail-close는 과거 증거 범위에서 `부분`이다.
4. Backup·Restore와 Local 손상 복구를 실제 Web·Windows 화면/API에서 확인하고 Browser Network의 Cloud 호출이 same-origin임을 증명하는 항목은 `미확보`다.
5. R1-M5-07의 자동·DB/Object/API 증거는 존재하지만 완료보고에는 `VERIFYING`과 과거 `BLOCKED` 기록이 함께 있고, Manifest도 `EXTERNAL_DATA_PASS_BROWSER_EVIDENCE_PENDING`이다. 이를 제품 `COMPLETED`로 승격할 수 없다.
6. 이번 소급 감사에서는 제품 테스트, Build, DB/Object/API, Server, Browser를 재실행하지 않았다. 역사 증거의 존재와 계보를 검증한 것이며 실제 여정 재검증이 아니다.

## M5 Exit 항목별 판정

| Exit 항목 | 판정 | 근거 | 남은 조건 |
| --- | --- | --- | --- |
| Local-private·Cloud-sync 별도 경로 | `부분` | M5-01·02 Cloud, M5-03 Local, M5-05 Sync 역사 증거 | 하나의 Exit 기준 재실행·대조 |
| 저장·전송 암호화와 Key 분리 | `부분` | RLS·SQLCipher·Secure credential·encrypted queue | Exit 수준 통합 Key-separation 증거 |
| 무승인 영역 이동·External 전송 0건 | `부분` | M5-05 승인·Offline 기록 | 실제 Browser Network 여정 |
| Web·Windows Backup/Restore·Local 복구와 same-origin | `미확보` | M5-07 API·DB/Object·Build 증거만 존재 | 실제 Web·Windows 화면, Network URL, Trace/Audit 연결 |
| Preview/Execute 권한·정책·Step-up·Allowlist·Purge 보호 | `부분` | M5-07 자동·서버 통합 기록 | 실제 운영형 사용자 여정 |
| G9-DRILL 전 운영 Restore·파괴 검증 Fail-close | `부분` | fixture-only·운영 미실행 기록 | G9-DRILL 승인 전 파괴 실행 금지 유지 |

## 조치

1. `R1-M5-07-WEB-EVIDENCE-01`을 별도 증거 보완 Work Order로 발행한다.
2. 기존 제품 코드·API·데이터 계약을 바꾸지 않고 현재 배포 SHA에서 Web Backup/Restore 화면과 same-origin Network를 실제 검증한다.
3. Windows 설치형 증거는 Web 검증과 분리한다. 과거 테스트 설치본은 검증 후 정상 제거됐고 현재 로컬에는 사용자 삭제 표시가 있으므로, 소스 복구·재빌드는 신산님 결정 없이 수행하지 않는다.
4. 검증은 전용 Fixture만 사용하고 운영 Restore·파괴적 손상 주입은 G9-DRILL 승인 전 실행하지 않는다.
5. Web 증거 보완 후 Windows 증거 수집 가능성을 별도 판정하고 R1-M5-07과 본 Exit 보고서를 재검토한다. 그 전까지 M5 Exit와 TP-2·TP-5 통과를 주장하지 않는다.

## 현재 위험

- 실제 화면·Network 증거 없이 API·Build 결과만으로 M5를 통과시키는 허위 완료 위험
- 현재 로컬 Working Tree의 제품 파일 삭제 표시 33건을 증거 수집 명목으로 복원하거나 Commit할 위험
- 과거 격리 서버 자원에 대한 무승인 Cleanup·Restore 위험
- Browser 화면만 캡처하고 Commit·Actor·Trace/Audit와 연결하지 않는 불완전 증거 위험

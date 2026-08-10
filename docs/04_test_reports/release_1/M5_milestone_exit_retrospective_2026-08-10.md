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
| Web Session 보정 Commit | `d0f0d0985120b78e8b6a0d32e22c69df12d3969e` |
| Web 화면 증거 Commit | `412e01455b723b34aea254033806bcdedca453e7` |
| 설계·기술 책임자 | 어울1 |

## 판정

`VERIFYING / NO_GO_TO_M5_EXIT`

M5 Evidence Manifest 소급 정합화와 Web Recovery 화면의 `ready` 상태 확인은 수락한다. 그러나 Browser Network 원본과 Windows 설치형 증거가 미확보이므로 M5 제품 Exit는 통과시키지 않는다. 이 판정은 TP-2·TP-5 독립 테스트 웨이브의 실행 또는 통과를 의미하지 않는다.

## 판단 이유

1. R1-M5-01~07 각각의 정규 `manifest.json`과 파일별 SHA-256·기록 Commit provenance가 존재하며, JSON `8/8`, 증거 경로·SHA `28/28`, recorded Commit `28/28`을 어울1이 재검증했다.
2. 내부 Task Review에서 최초 Important 2건을 보완했고, scoped Re-review와 post-commit provenance Review가 모두 승인됐다. 이는 내부 검토이며 CLAUDE 외부 독립 검증을 대신하지 않는다.
3. 계획 §15의 M5 Exit 항목 6개 중 Local·Cloud 분리, 암호화·Key 분리, 무승인 영역 이동 0건, Preview/Execute 보안, G9-DRILL 전 Fail-close는 과거 증거 범위에서 `부분`이다.
4. Web Session의 실제 Tenant·Workspace를 Recovery 화면에 전달하도록 `R1-M5-07-C02`를 보정했고, ysna-server에서 Commit `d0f0d098...`을 Build·배포했다. Web image `sha256:117cfd39...`와 API image `sha256:91b45461...`은 Healthy 상태였다.
5. 로그인된 실제 `/operations` 화면에서 Recovery 영역 `ready`, Backup 행 0건, 오류 Trace 0건을 확인했다. 원본 PNG·DOM과 SHA는 `R1-M5-07-WEB-EVIDENCE-01`에 보존됐다.
6. 공개 Browser Client가 요청 URL·Method·Status를 제공하지 않고 페이지 Context에도 Resource Timing이 노출되지 않아 same-origin과 내부 주소 직접 호출 0건은 `NOT_PROVEN`이다. 화면 PASS를 Network PASS로 확대하지 않는다.
7. Windows 설치형 Backup/Restore·Local 복구 증거는 미확보다. 현재 Working Tree의 사용자 삭제 33건을 신산님 승인 없이 복원하지 않았으며, 운영 Restore·파괴 검증도 실행하지 않았다.
8. 따라서 R1-M5-07의 자동·DB/Object/API·Server·Web 화면 증거는 보강됐지만 M5 제품 `COMPLETED`로 승격할 수 없다.

## M5 Exit 항목별 판정

| Exit 항목 | 판정 | 근거 | 남은 조건 |
| --- | --- | --- | --- |
| Local-private·Cloud-sync 별도 경로 | `부분` | M5-01·02 Cloud, M5-03 Local, M5-05 Sync 역사 증거 | 하나의 Exit 기준 재실행·대조 |
| 저장·전송 암호화와 Key 분리 | `부분` | RLS·SQLCipher·Secure credential·encrypted queue | Exit 수준 통합 Key-separation 증거 |
| 무승인 영역 이동·External 전송 0건 | `부분` | M5-05 승인·Offline 기록 | 실제 Browser Network 원본 |
| Web·Windows Backup/Restore·Local 복구와 same-origin | `부분` | Web Recovery `ready`·빈 목록 원본 PNG/DOM, API·DB/Object·Build 증거 | Network URL·Method·Status와 Windows 설치형 증거 |
| Preview/Execute 권한·정책·Step-up·Allowlist·Purge 보호 | `부분` | M5-07 자동·서버 통합 기록, 실제 Session Context fail-close 보정 | 실제 운영형 권한 사용자 여정 |
| G9-DRILL 전 운영 Restore·파괴 검증 Fail-close | `부분` | fixture-only·운영 미실행 기록 | G9-DRILL 승인 전 파괴 실행 금지 유지 |

## 조치

1. `R1-M5-07-WEB-EVIDENCE-01`의 화면 증거 보완은 `SCREEN_READY_EMPTY_LIST_NETWORK_UNPROVEN`으로 수락한다.
2. Browser Network URL·Method·Status와 same-origin·내부 주소 직접 호출 0건은 원본을 수집할 수 있는 승인된 수단으로 별도 검증한다.
3. Windows 설치형 증거는 Web 검증과 분리한다. 현재 로컬의 사용자 삭제 표시 33건은 신산님 결정 없이 복원·Commit하지 않는다.
4. 검증은 전용 Fixture만 사용하고 운영 Restore·파괴적 손상 주입은 G9-DRILL 승인 전 실행하지 않는다.
5. Network와 Windows 증거를 확보한 뒤 R1-M5-07과 본 Exit 보고서를 재검토한다. 그 전까지 M5 Exit와 TP-2·TP-5 통과를 주장하지 않는다.

## 현재 위험

- 실제 화면 증거를 Network·Windows 증거로 확대 해석해 M5를 통과시키는 허위 완료 위험
- 현재 로컬 Working Tree의 제품 파일 삭제 표시 33건을 증거 수집 명목으로 복원하거나 Commit할 위험
- 과거 격리 서버 자원에 대한 무승인 Cleanup·Restore 위험
- Browser 화면만 캡처하고 Commit·Actor·Trace/Audit와 연결하지 않는 불완전 증거 위험

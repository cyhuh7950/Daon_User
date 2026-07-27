# R1-M3-06-C26 수정 작업지시서 — Permission XCTest 안전 실패 Annotation

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `27` |
| 사유 | C25 exact-SHA가 첫 Permission XCTest에서 Exit 65로 실패했으나 GitHub 상세 Log·Artifact 다운로드 인증 만료로 실패 Assert를 확정할 수 없음 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-27.md` |

## 2. 확인된 증거

- exact Head `68b50efd7d058767a50840471030c5f780c172f6`의 Quality Gate Run `30257206564`은 SUCCESS다.
- iOS Run `30257206671`은 Build와 선행 UI Test까지 SUCCESS이고 Permission Step이 약 97초 후 XCTest Exit 65로 실패했다.
- 이는 unsupported notifications privacy 호출을 통과해 `grant-initial`의 실제 Production 권한 XCTest에 진입한 결과다.
- Check Annotation에는 Exit 65만 있고, GitHub CLI Token과 격리 Browser 모두 인증되지 않아 xcresult 세부 Assertion을 읽을 수 없다.
- 현재 근거로 Alert Selector나 앱 결과를 추측 수정하지 않는다.

## 3. 필수 작업

1. Permission Phase의 `xcodebuild test-without-building` 출력과 원 Exit를 Phase별 Evidence Log에 보존하면서 Console 출력도 유지한다.
2. 실패 시 Raw 문장·경로·UDID·사용자 데이터·URL을 Annotation에 출력하지 않고, 승인된 XCTest Assertion 문자열을 고정 코드로 매핑해 정확히 한 줄의 GitHub Error Annotation을 출력한다.
3. 최소 코드는 Alert 제목/개수/Allow 버튼/Alert 종료, Settings foreground/Notification Row/Switch/값 전환, 앱 복귀 Root, Production 권한 결과 누락을 서로 구분한다. 어떤 승인 문자열에도 일치하지 않으면 `UNKNOWN_XCTEST_FAILURE`로 Fail-close한다.
4. Annotation에는 `<allowlisted failure code>`, `<grant-initial|revoke|grant-again>`, 숫자 원 Exit만 포함한다.
5. `PIPESTATUS`로 원 xcodebuild Exit를 보존하고 `set -Eeuo pipefail`·ERR/EXIT Trap·xcresult·Evidence Upload를 유지한다. 진단 실패가 원 테스트 결과를 성공으로 바꾸지 않는다.
6. C25의 Alert/Settings Selector·Timeout·Phase·권한/제품 동작은 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 알려진 Assertion Exit 65 Fixture의 안전 Annotation·원 Exit 보존 계약 RED
- 구현 후 알려진 Assertion은 승인 Code·Phase·65만, 미지 Assertion은 UNKNOWN·Phase·65만 출력하고 원 Exit 65 유지
- 성공 Fixture는 실패 Annotation 0건이고 출력/xcresult 계약 유지
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Simulator Script, 관련 계약 Test, Progress와 Attempt 27 보고서뿐이다.
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속이다.


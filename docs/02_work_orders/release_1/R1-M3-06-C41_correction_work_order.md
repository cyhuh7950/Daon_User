# R1-M3-06-C41 수정 작업지시서 — Settings 접근성 진단 공개 Annotation

## 판정

- `issue_id`: `R1-M3-06-I007`, Attempt `42`, 정식 실패 0회.
- 근거: Head `87b2532a5da412ae0911651dbc8c1a37ada6b1ad`, Run `30288930289`, Job `90053595796`은 Build·일반 UI Test 성공 후 revoke에서 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO / 65`로 종료했다.
- C37~C40으로 direct exact·semantic Cell·exact Label·delimiter-anchored composite Cell과 최대 4회 bounded scroll이 모두 0건임을 확인했다. 추가 Selector 추측 전에 실제 Settings 접근성 표면 증거가 필요하다.

## 목적

격리된 iOS Simulator의 Daon 앱 설정 화면에서 Notifications 행 탐색이 최종 0건일 때만, 접근성 요소의 안전한 구조 요약을 XCTest 로그에 고정 형식으로 출력하고 GitHub 공개 Annotation으로 변환한다. 제품 코드·권한 동작·현재 Selector는 변경하지 않는다.

## 필수 작업

1. `requireExactNotificationSettingsRow`가 bounded scroll 이후 최종 `COMPOSITE_ZERO`로 실패하기 직전에만 진단을 1회 생성한다.
2. 진단 대상은 현재 Settings 앱의 `cell`, `button`, `staticText`, `switch` 접근성 요소로 제한한다. 각 항목은 `elementType`, 정규화된 `label`, `identifier`, `isHittable`만 포함한다.
3. 값은 단일 행 안전 문자로 정규화한다. 줄바꿈·제어문자·GitHub Workflow 명령 구문(`::`)을 제거하거나 치환하고, 항목당 길이와 전체 항목 수를 고정 상한으로 제한한다. 빈 값은 명시적 토큰으로 표현한다.
4. 출력은 파서가 정확히 식별할 수 있는 단일 고정 Prefix와 한 줄 형식으로 한다. 원시 `debugDescription`, 화면 전체 Dump, 환경변수, 경로, 사용자 데이터, 토큰·비밀값 출력은 금지한다.
5. `verify-simulator.sh`는 revoke/grant-again Permission XCTest 실패 시 해당 고정 Prefix의 마지막 한 줄만 읽어 별도 `::notice::` Annotation으로 출력한다. 기존 `::error::CODE=... PHASE=... EXIT=...` Annotation과 Exit Code는 그대로 유지한다.
6. Notice 내용도 Bash에서 길이·문자 집합을 다시 검증하고, 불일치하면 Notice를 생략한 채 기존 Error만 Fail-close로 유지한다.
7. 진단 부재·다중 진단·개행/Workflow 명령 주입·과다 항목·과다 길이·정상 성공 케이스를 계약 Test로 먼저 RED 고정한 뒤 최소 구현한다.
8. 기존 direct exact·semantic·composite·bounded scroll Query, 단일성 우선순위, Alert/Switch, Product UI/Native Bridge/API/Dependency/Lockfile/Workflow를 변경하지 않는다.
9. `npm run verify:ios-native`, Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check`와 변경 경계 검사를 수행한다. 기존 Quality Gate의 별도 실패는 이번 작업으로 완화하지 않는다.
10. 착수·RED·GREEN·회귀·종료 직전에 `docs/04_test_reports/release_1/R1-M3-06_progress.md`를 갱신하고 `docs/02_work_orders/reports/R1-M3-06_attempt-42.md`를 작성한다.

## 금지

- 새로운 Label·identifier·부분문자열·정규식 Selector 추가
- 좌표·Index·`firstMatch`·무제한 반복·sleep
- Apple 비공개 URL·Defaults/TCC 직접 조작·재설치
- 제품 UI·Native Bridge·공개 API·권한 의미 변경
- Commit·Push·PR·GitHub 실행·SSH·서버·GUI·Signing

## 완료 보고

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식을 사용한다.

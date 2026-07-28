# R1-M3-06-C43 수정 작업지시서 — 알림 설정 Open 결과·권한 상태 진단

## 판정

- `issue_id`: `R1-M3-06-I007`, Attempt `44`, 정식 실패 0회.
- 근거: Head `99ece954bf7cf25014fb144acd7fbb94469aac17`, iOS Run `30295391636`, Job `90075005931`은 새 Bridge를 포함한 Build·일반 UI Test 성공 후 revoke에서 다시 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO / 65`로 종료했다.
- C41 안전 Notice는 전역 Settings 화면을 다시 확인했다. 현재 증거만으로 Apple 공개 URL `open()` 완료값과 호출 직전 알림 권한 상태를 구분할 수 없다.

## 목적

제품 UI·Bridge 공개 계약·Selector를 변경하지 않고, 알림 설정 공개 URL 호출 직전 권한 상태와 `UIApplication.open` 완료 Bool을 안전한 고정 Marker로 기록해 Permission 실패 시 공개 CI Notice로 회수한다.

## 필수 작업

1. 기존 `openNotificationSettings` 내부에서 `UNUserNotificationCenter.current().getNotificationSettings`로 호출 직전 `authorizationStatus`를 읽고 기존 `notificationPermissionState`의 고정 값으로 정규화한다.
2. iOS 16+/15.1 URL 선택과 `UIApplication.shared.open` 의미는 유지한다. Completion에서만 `DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=<OPENED|FAILED> AUTH=<GRANTED|DENIED|NOT_REQUESTED|RESTRICTED>`를 `NSLog` 한 줄로 기록하고 기존 Bool을 Resolve한다.
3. URL 생성 실패 Reject Code, 기존 generic 메서드, Bridge Selector 8개, UI와 XCTest Selector를 변경하지 않는다.
4. `verify-simulator.sh`는 Permission XCTest 실패 때만 Simulator unified log에서 Daon Process의 마지막 5분·정확 Marker를 별도 임시 파일로 수집한다. 마지막 유효 한 줄 한 건만 고정 문자·길이·값을 재검증해 `::notice::`로 출력한다.
5. Log 수집 실패·Marker 부재·다중 중 마지막 invalid·주입·허용 외 값은 Notice를 생략하고 기존 Error·Exit 65를 유지한다. 전체 Raw Log·경로·환경·비밀값은 Annotation에 출력하지 않는다.
6. RED Test는 Swift 권한 조회→URL Open→Marker/Resolve 순서와 Bash OPENED/FAILED·4 Auth 값·부재·주입·수집 실패·기존 Error/Exit 보존을 고정한다.
7. `npm run verify:ios-native`, Mobile·Android·전체 Node·Toolchain·Workflow/Bash·Bundle·`git diff --check`와 변경 경계를 검증한다.
8. 단계별 Progress와 `docs/02_work_orders/reports/R1-M3-06_attempt-44.md`를 작성한다.

## 허용 변경

- `apps/mobile/ios/Daon/DaonIOSHost.swift`
- `apps/mobile/ios/ci/verify-simulator.sh`
- `scripts/tests/ios-native-shell.test.mjs`
- C43 작업지시·Prompt·Progress·Attempt 문서

## 금지

- Product UI·Bridge Selector/API·Settings Selector·권한 의미 변경
- `registerForRemoteNotifications`, APNs Capability·Entitlement·Signing 추가
- 비공개 URL·TCC/Settings DB·재설치·Test 성공 변환
- Workflow·Dependency·Lockfile·Project 변경
- Commit·Push·PR·GitHub 실행·SSH·서버·GUI·Signing

## 완료 보고

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식을 사용한다.

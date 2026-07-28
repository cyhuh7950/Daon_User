# R1-M3-06-C42 수정 작업지시서 — iOS 알림 설정 전용 공식 진입

## 판정

- `issue_id`: `R1-M3-06-I007`, Attempt `43`, 정식 실패 0회.
- 승인: 신산님이 2026-07-28 기존 범용 앱 설정을 보존하면서 iOS 알림 설정 전용 버튼·브리지와 iOS 15.1 Fallback을 추가하는 방안을 승인했다.
- 근거: Head `92e1f6a8a24022fe4854fd074085946a53f1f15b`, iOS Run `30292083384`, Job `90064002676`은 Build·일반 UI Test 성공 후 revoke에서 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO / 65`로 종료했다. C41 Notice의 16개 요소는 `Apple Intelligence & Siri`, `Camera`, `Home Screen & App Library`, `Search` 등 전역 Settings 화면을 나타내며 Daon 앱 알림 설정 화면에 도달하지 않았음을 입증한다.
- 정본 갱신: 상세 설계 결정 기록과 Release 1 `R1-D021`에 승인 결정을 반영했다.

## 사용자 관점 완료 조건

1. 기존 `앱 권한 설정` 버튼은 그대로 존재하고 기존 범용 `openApplicationSettings()` 동작을 유지한다.
2. iOS에는 별도 `알림 설정` 버튼이 표시되며 실제 탭하면 Daon의 알림 설정 화면으로 이동한다.
3. iOS 16 이상은 Apple 공개 `UIApplication.openNotificationSettingsURLString`을 사용하고, 최소 지원 iOS 15.1에서는 기존 `UIApplication.openSettingsURLString`으로 Fallback한다.
4. Simulator의 동일 설치에서 grant-initial → 알림 설정 OFF → Production 요청 DENIED → 알림 설정 ON → Production 요청 GRANTED가 실제 UI와 상태로 검증된다.

## 필수 구현

1. `NativePermissionAdapter`에 iOS가 제공할 선택적 `openNotificationSettings(): Promise<void>` 계약을 추가한다. 기존 required 메서드와 Android Adapter 동작은 변경하지 않는다.
2. `ios-host.ts`와 `DaonIOSHostBridge.m`/`DaonIOSHost.swift`에 `openNotificationSettings`를 1:1 연결한다. Bridge Export는 승인 8개 Selector만 허용한다.
3. Swift는 `if #available(iOS 16.0, *)`에서 `UIApplication.openNotificationSettingsURLString`, 그 이전에는 `UIApplication.openSettingsURLString`을 선택한다. URL 생성 실패는 알림 설정 전용 고정 Error Code로 Reject하고, `UIApplication.shared.open`의 Bool 결과를 그대로 Resolve한다.
4. `MobileShell`은 Adapter가 알림 전용 메서드를 제공할 때만 `알림 설정` 텍스트와 `알림 설정 열기` 접근성 Label의 실제 버튼을 표시한다. 기존 세 권한 요청 버튼과 `앱 권한 설정` 버튼, 1920×1080·12px 화면 표준을 유지한다.
5. iOS Permission XCTest의 revoke·grant-again은 새 Production `알림 설정 열기` 버튼을 탭한다. 먼저 현재 화면의 exact `Allow Notifications`/`알림 허용` Switch를 찾고, iOS 15.1 Fallback처럼 직접 화면이 아닐 때만 기존 exact Notifications 행 Helper를 사용해 들어간다.
6. Settings 앱 foreground만으로 성공 처리하지 않고, 알림 Switch 또는 기존 exact Notifications 행이라는 앱별 표면이 나타나야 다음 단계로 진행한다. 기존 좌표·Index·`firstMatch`·부분문자열·무제한 반복 금지와 단일성 Fail-close를 유지한다.
7. 기존 C41 접근성 진단은 Fallback 경로 최종 행 0건에서만 유지한다. 새 전용 경로 성공 시 Notice가 생성되지 않아야 한다.
8. TDD 순서는 Bridge 8개·iOS 16/15.1 URL 선택·선택적 버튼·기존 generic 보존·direct Switch 우선/Fallback 행·3 Phase·금지 패턴 계약 RED → 최소 GREEN이다.
9. `npm run verify:ios-native`, Mobile·Android·전체 Node·Toolchain·Workflow/Bash·Bundle·`git diff --check`와 Product/Project/Package/Lock 변경 경계를 검증한다.
10. 착수·RED·GREEN·오류 복구·회귀·종료 직전에 `docs/04_test_reports/release_1/R1-M3-06_progress.md`를 기록하고 `docs/02_work_orders/reports/R1-M3-06_attempt-43.md`를 작성한다.

## 허용 변경

- `apps/mobile/src/MobileShell.tsx`
- `apps/mobile/src/platform/ios-host.ts`
- `apps/mobile/ios/Daon/DaonIOSHost.swift`
- `apps/mobile/ios/Daon/DaonIOSHostBridge.m`
- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
- 관련 Mobile/iOS 계약 Test, 승인 설계·계획·작업지시·Progress·Attempt 문서

## 금지

- 기존 `openApplicationSettings()` 제거·의미 변경
- Android 기능·Adapter·권한 의미 변경
- Apple 비공개 URL Scheme, Settings Defaults·TCC·`simctl notifications` 직접 조작, 재설치
- 좌표·Index·`firstMatch`·부분문자열 Selector·Test 성공 변환
- Dependency·Lockfile·Workflow·Bundle ID·Deployment Target·Signing 변경
- Commit·Push·PR·GitHub 실행·SSH·서버·GUI·Signing

## 완료 보고

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식을 사용한다.

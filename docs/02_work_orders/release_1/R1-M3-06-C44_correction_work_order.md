# R1-M3-06-C44 수정 작업지시서 — iOS 26 Simulator Settings Search Fallback

## 판정

- `issue_id`: `R1-M3-06-I007`, Attempt `45`, 정식 실패 0회.
- Head `b1cadde`, Run `30313465208`, Job `90133918323`에서 `DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=OPENED AUTH=GRANTED`와 global Settings를 확인했다.
- 제품 공개 URL·권한은 정상이고 iOS 26 GitHub-hosted Simulator의 resource navigation 한계로 판정한다.

## 목적

제품 코드 변경 없이 공식 URL 뒤 `direct exact Allow Notifications Switch → 기존 exact Notifications row → iOS 26에서만 Settings Search fallback` 순서로 Daon 앱 설정에 진입한 후 `OFF/DENIED→ON/GRANTED`를 검증한다.

## 필수 계약

1. 기존 direct exact Switch와 exact Notifications row 우선순위를 유지한다.
2. Search는 `#available(iOS 26.0, *)`에서만 사용하고 이전 iOS는 Fail-close한다.
3. exact identifier `com.apple.settings.search` Button이 정확히 1건이고 Hittable일 때만 탭한다.
4. `settings.searchFields`의 Hittable 후보가 정확히 1건이어야 하며 기존 Text를 안전하게 삭제한 뒤 고정 `Daon`을 입력한다.
5. exact label `Daon`의 Hittable Cell을 우선하고, 없을 때만 exact label Hittable Element를 사용하며 후보는 정확히 1건이어야 한다.
6. 검색 결과 뒤 direct exact Switch를 우선하고, 없으면 기존 exact Notifications row 뒤 exact Switch를 검증한다.
7. 고정 Wait·Tap만 사용하고 `sleep`·재설치·TCC·Defaults·비공개 URL을 금지한다.
8. Search Button·Field·Result·App Surface별 고정 Stage·Assertion Code와 Bash Allowlist를 추가하고 기존 Error·Exit 65를 보존한다.
9. C41·C43 진단, 공식 URL, `OPENED AUTH=GRANTED` Marker, 제품 알림 설정 Button, generic 설정과 iOS 15.1 fallback을 유지한다.
10. TDD 후 `npm run verify:ios-native`, Mobile·Android·전체 Node·Toolchain·Workflow/Bash·Bundle·`git diff --check`·보호 경계를 검증한다.
11. 단계별 Progress와 `docs/02_work_orders/reports/R1-M3-06_attempt-45.md`를 작성한다.

## 허용 변경

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
- `apps/mobile/ios/ci/verify-simulator.sh`
- `scripts/tests/ios-native-shell.test.mjs`
- C44 작업지시·Prompt·Progress·Attempt 문서

## 금지

- Product Source·Native Host·Bridge/API·권한 의미 변경
- iOS 26 외 Search
- 좌표·Index·`firstMatch`·부분문자열·Regex Label·무제한 반복
- Skip·성공 변환·direct path 성공 주장
- Workflow·Dependency·Lockfile·Project·Signing 변경
- Commit·Push·PR·GitHub 실행·SSH·서버·GUI·Signing

## 완료 보고

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식을 사용한다.

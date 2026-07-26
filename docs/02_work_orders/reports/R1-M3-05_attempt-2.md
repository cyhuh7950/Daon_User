COMPLETED | R1-M3-05-I001 | 승인 Android Deep Link 완결·Desktop 실패 원인 분리·전체 회귀 수행 | Exact Allowlist Parser·Manifest Filter·정적/ADB 증거·Debug/unsigned Release APK 생성 | Android 11/11·정상 Deep Link 8/8·비정상 11/11·공통 Gate 33/33 PASS | Phase B 실기기·Upload Keystore·서명 Release APK 미수행 | 어울1의 Phase A 검토와 `SIMULATOR_VERIFIED_PENDING_DEVICE` 판정

# R1-M3-05 Attempt 2 결과보고

## 판정

R1-M3-05-C01 개발 패킷은 `COMPLETED`다. 신산님이 승인한 `sinsan-daon://app/<native_route_key>` 계약을 정확 비교와 기존 Android Route 8개 Allowlist로 구현했고, 정적 계약·APK·Emulator 실제 입력·상태 복원·전체 회귀를 모두 통과했다. Attempt 1의 Desktop Rust 실패는 생성물·관련 Process가 없는 깨끗한 상태에서 14/14 통과했고, 공통 7범주 Quality Gate도 33/33 통과해 Android 변경과 무관한 비결정적 환경 변동으로 분리했다.

전체 Work Order는 Phase A 완료 후보인 `SIMULATOR_VERIFIED_PENDING_DEVICE`다. Phase B의 Android 12+ 실기기, Daon 전용 Upload Keystore Ceremony, 서명 Release APK 검증은 수행하지 않았다. Commit·Push·PR·Merge·SSH·배포·Release Keystore 생성은 하지 않았다.

## 판단 이유

### 변경 결과

- 지정 Worktree `C:\tmp\Daon_User-r1-m3-05`, Branch `codex/r1-m3-05`, 시작 HEAD `f2a5a923763bfdf9bf4b1b41a270db9c3fe536c8`에서 단독 Writer로 Attempt 1 변경을 보존해 이어서 작업했다.
- `apps/mobile/src/platform/android-deep-link.ts`에 승인 Prefix의 byte-exact 비교와 Android 정본 Projection의 Route Set 조회를 추가했다. URL Parser의 Scheme·Host 정규화를 사용하지 않아 대소문자·Encoding 우회를 수락하지 않는다.
- `android-host.ts`가 승인 Parser를 사용하도록 연결하고, Manifest에 `VIEW`·`DEFAULT`·`BROWSABLE`, Scheme `sinsan-daon`, Host `app`, Path Prefix `/`만 추가했다.
- 정상 Route 8개와 Scheme 대소문자, 다른/대문자 Host, 빈/추가 Path, 미등록 Route, 단일·경로·이중 Encoding, Query·Fragment 등 대표 비정상 입력을 별도 계약 Test로 고정했다.
- 기존 8 Route·7 State·15 Action, Permission·Lifecycle·Route 복원, same-origin/내부 URL 금지, Release unsigned 경계는 유지했다. Desktop/Local Service Source는 변경하지 않았다.

### APK·Emulator 근거

- Debug APK: `45,921,467 bytes`, SHA-256 `3838FB71456738DD396A42A404D2E23F4795C436D342D26A73D4DBBCDF972B28`, APK Signature v2 Debug signer.
- Release APK: `25,487,221 bytes`, SHA-256 `BA6192C24E1751067435C61F00FCBF6C49C0C7B4F8B540BE0464A0FB48ECA5BB`, Phase B 전 의도된 `app-release-unsigned.apk`. Release Manifest의 `SYSTEM_ALERT_WINDOW`는 0건이다.
- `emulator-5554`, Android 16/API 36에서 최종 Debug APK 설치 성공.
- 정상 Deep Link `Home`, `WorkspaceList`, `WorkspaceDetail`, `Inbox`, `RunHistory`, `Notifications`, `ModelConnections`, `AccountSettings`는 ADB 시작과 실제 화면 Title까지 8/8 통과했다.
- 비정상 11종은 11/11 Fail-close했다. OS가 거부한 Scheme case·다른 Host 외에 Activity로 전달된 Host case·빈/추가 Path·Unknown Route·Encoding 3종·Query·Fragment도 Parser가 거부해 기준 `AccountSettings` Route를 보존했다.
- Force-stop→Cold `Notifications`, Home→Resume, Force-stop→Launcher 복원은 3/3 통과했다.
- 최종 격리 Logcat에서 App UID 기준 Crash/ANR 0, Secret Assignment 0. 앱 PID 0·Resumed Activity 0이며 사용자가 실행한 Emulator는 연결 상태로 유지했다.

### 테스트 결과

| 검증 | 결과 |
| --- | --- |
| Deep Link TDD | 구현 전 Module Not Found RED → 구현 후 2/2 PASS |
| Android 전용 Gate | 11/11 PASS |
| Gradle Clean·Compile/Unit·Lint | PASS, Unit Source는 NO-SOURCE이며 Kotlin Compile 성공 |
| Gradle Assemble Debug·Release | PASS |
| Mobile Lint·Type·Unit·Contract·Android/iOS Bundle | PASS, Unit 9/9·Contract 15/15 |
| 전체 Node Test | 262/262 PASS |
| Toolchain·Independence | PASS, Independence violations 0 |
| Production Audit High | Exit 0. React Native CLI 전이 `fast-xml-parser` Moderate 10건, 공개 Fix 없음 |
| Desktop Clean 재현 | Node 25/25·Rust Manager 14/14·Contract 3/3 PASS |
| 공통 7범주 Quality Gate | PASS, 33/33: lint 7·type 4·unit 8·contract 3·build 7·security 3·independence 1 |

Desktop 재현 전후 Android App PID, Desktop `gen`, 격리 Target, Fixture Marker, 관련 Process는 모두 0이었다. Attempt 1에서 실패한 `production_manager_error_fixtures_are_bounded_and_leave_no_processes`도 통과했고 전체 Gate에서 재발하지 않았다.

### 오류·복구 근거

- Independence가 테스트의 추가 Path URL 문자열을 외부 절대경로로 오인한 1건은 실행 시 동일 URL을 만드는 분할 표현으로 최소 정정했다. 실제 거부 입력·기대값과 검사 정책은 바꾸지 않았고 재실행 `violations=0`을 확인했다.
- 공통 Gate는 시작 전 Coverage, Android 생성 Cache, Desktop Target·Fixture Marker·관련 Process 0에서 실행했다. 331.4초 후 Exit 0·Failures 0으로 종료했다.
- Gate가 갱신한 공통 R1-M1-04/R1-M1-05 Evidence와 `.coverage`는 결과를 기록한 뒤 시작 HEAD로 원복·삭제해 관련 없는 변경을 남기지 않았다.

## 조치

1. 어울1은 본 보고서, Progress, Diff와 33/33 Gate 근거를 검토해 Phase A `SIMULATOR_VERIFIED_PENDING_DEVICE` 여부를 판정한다.
2. 승인 시 후속 Phase B에서 Android 12+ 실기기, Permission 영구 거부·Settings 왕복, Background/Process Death, Deep Link, Daon 전용 Upload Keystore·서명 Release APK를 별도 Gate로 수행한다.
3. Attempt 2는 정식 실패보고가 아니며 `R1-M3-05-I001` failure count는 0이다.

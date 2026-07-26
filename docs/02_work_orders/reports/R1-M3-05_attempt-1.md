BLOCKED | R1-M3-05-I001 | Android Native Project·Host Adapter·전용 Gate 구현 및 Emulator 핵심 흐름 검증 | Debug·unsigned Release APK와 진행·검증 근거 생성 | Android 전용 Gate 9/9·전체 Node 260/260·Mobile 표준 회귀 PASS·공통 Gate 32/33 | Deep Link Scheme/Host 미승인, 기존 Desktop Rust Unit 1건 실패 | 신산님의 Deep Link 계약 승인과 어울1의 공통 Gate 선행 결함 처리 판단

# R1-M3-05 Attempt 1 결과보고

## 판정

Phase A는 `BLOCKED`다. Android Native Shell의 구현·APK·Emulator 검증은 Deep Link를 제외하고 완료했지만, 승인된 Scheme/Host가 없어 필수 Deep Link 흐름을 수행하지 않았다. 또한 공통 7범주 Quality Gate가 Android 범위 밖의 기존 Desktop Rust Unit 1건으로 실패했으므로 `SIMULATOR_VERIFIED_PENDING_DEVICE`를 선언하지 않는다. 정식 개발 실패보고가 아니며 failure count는 0이다.

## 수행·변경 결과

- 지정 Worktree `C:\tmp\Daon_User-r1-m3-05`, Branch `codex/r1-m3-05`, 시작 HEAD `f2a5a923763bfdf9bf4b1b41a270db9c3fe536c8`에서 단독 Writer로 작업했다.
- React Native Community Template `0.86-stable`, Commit `4d7c716d7afddc03ed73ca49c1102a92a0a9ff71`을 기준으로 `apps/mobile/android/**`를 생성했다.
- Application ID `com.sinsan.daon`, 표시명 `Daon`, minSdk 31, compile/targetSdk 36, x86_64, Hermes, Edge-to-Edge를 고정했다.
- Camera·Microphone·Notification 최소 권한, Route 저장·복원, Lifecycle, App Settings 이동 Native Module을 구현했다. Storage 권한·API 주소·Credential·Release Debug Signing은 추가하지 않았다.
- 기존 8 Route·7 State·15 Mobile Studio Action·Public API unavailable 계약을 유지하고 `App.tsx`, `MobileShell.tsx`, `android-host.ts`에서 Android Host만 주입했다.
- Root Android 전용 Gate와 Gradle 실행 Wrapper를 추가했다. 기존 Mobile Workspace 다섯 명령은 정확한 기존 계약을 보존했다.
- 변경 범위: `apps/mobile/android/**`, `apps/mobile/app.json`, `apps/mobile/package.json`, `apps/mobile/src/App.tsx`, `apps/mobile/src/MobileShell.tsx`, `apps/mobile/src/platform/android-host.ts`, `package.json`, `package-lock.json`, `scripts/run-android-gradle.mjs`, `scripts/tests/android-native-shell.test.mjs`, Progress와 본 보고서다.

## APK·Emulator 근거

- Debug APK: `45,920,577 bytes`, SHA-256 `26B83FD25BA7871D46A886802F41E82C92E218190DFAC575E49047B0CAD542E7`, APK Signature v2, Android Debug signer 1개.
- Release APK: `25,486,345 bytes`, SHA-256 `7E549571E15AF61AA0FE062076332C3AE4097585405AAF9D7CF7FA973815ED0A`, 의도된 unsigned 산출물. Phase B Keystore Ceremony 전이라 설치·Release 완료 근거로 사용하지 않았다.
- `emulator-5554`, Android 16/API 36에서 Debug APK Install·Cold Launch 성공.
- ADB 실제 입력으로 Home, WorkspaceList, WorkspaceDetail, Inbox, RunHistory, Notifications, ModelConnections, AccountSettings 8/8 전환 성공.
- `Notifications` 선택 후 Home→Resume와 `am force-stop`→Relaunch 모두 Route 복원 확인.
- Camera 허용, Microphone 거부·재요청·영구 거부, Notification 허용, `com.android.settings/.spa.SpaActivity` 권한 설정 이동과 App 복귀 확인.
- 최종 Logcat Crash Buffer 0, FATAL/ANR 0, Credential형 Secret Assignment 0. 종료 시 App PID 0·Resumed Activity 0이며 Emulator는 중지하지 않았다.

## 테스트 결과

| 검증 | 결과 |
| --- | --- |
| TDD RED→GREEN | 구현 전 Android 0/9 실패 → 구현 후 9/9 PASS |
| `npm ci --ignore-scripts` | Exit 0, 507 packages |
| Mobile Lint·Type | Exit 0 |
| Mobile Unit·Contract | 9/9, 15/15 PASS |
| Android 전용 Gate | 9/9 PASS |
| Android·iOS Production Bundle | PASS |
| 전체 Node Test | 260/260 PASS |
| Gradle Clean·Compile/Unit Task | PASS, Unit Source는 NO-SOURCE이며 Kotlin Compile 성공 |
| Gradle Lint·Assemble Debug·Assemble Release | PASS |
| Toolchain·Independence | PASS, Independence violations 0 |
| Production Audit High | Exit 0. React Native CLI 전이 `fast-xml-parser` Moderate 10건, 공개 Fix 없음 |
| 공통 7범주 Quality Gate | FAIL, 33 Checks 중 32 PASS·1 FAIL |

공통 Gate 유일 실패는 기존 Desktop Rust Test `production_manager_error_fixtures_are_bounded_and_leave_no_processes`의 `state did not become ready`다. 승인 실행 Gate와 독립 재실행에서 두 번 동일 재현됐으며 Node Desktop 계약은 25/25, Rust는 13/14 통과했다. Android 변경 범위 밖이라 수정하지 않았다.

## 오류·복구 근거

- Gradle Wrapper `.bat` spawn `EINVAL`은 Windows `shell:true`로 복구했다.
- Kotlin `currentActivity` 참조 오류는 `context.currentActivity`로 수정했다.
- Android Lint의 Camera Hardware Feature 오류는 Camera·Microphone Feature를 `required=false`로 명시해 복구했다.
- Release Hermes 경로 2회 오류는 React Native Gradle Plugin Path 해석과 hoisted Compiler 실제 위치를 확인해 `../../node_modules/hermes-compiler/hermesc/%OS-BIN%/hermesc`로 복구했다.
- 기존 Mobile `contract` Script에 Android Gate를 결합한 회귀는 기존 Unit Test가 검출했다. 표준 Script를 원복하고 Android 전용 Gate를 분리했다.
- Independence의 Android `.cxx` SDK 절대경로 136건은 Source가 아닌 생성 캐시임을 확인하고 해당 캐시만 삭제한 뒤 violations 0을 확인했다.
- 공통 Gate Sandbox 실행의 Coverage SQLite 권한 오류는 Sandbox 밖 승인 실행으로 복구했다. 공통 Gate가 최종적으로 도달한 1건 실패는 별도 Desktop Rust 결함이다.

## 미해결·필요 판단

1. 승인된 Deep Link Scheme/Host가 없다. 어울1 권고안 `sinsan-daon://app/<native_route_key>`을 신산님이 승인해야 Manifest·Allowlist Parser·ADB 정상/비정상 Deep Link를 구현·검증할 수 있다.
2. 공통 Gate의 기존 Desktop Rust Unit 1건을 R1-M3-05 진입 선행 결함으로 처리할지, 해당 Owner에게 재작업할지 어울1 판단이 필요하다.
3. 위 2건 해소 후 S6 Deep Link와 S8 공통·전용 Gate를 재실행해야 Phase A `SIMULATOR_VERIFIED_PENDING_DEVICE`를 선언할 수 있다.
4. Phase B Android 12+ 실기기·Daon 전용 Upload Keystore·서명 Release APK는 후속 최종 Gate로 남는다.

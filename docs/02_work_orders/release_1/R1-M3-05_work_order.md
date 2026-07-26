# R1-M3-05 작업지시서 — Android 설치 Shell

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M3-05` |
| issue_id | `R1-M3-05-I001` |
| depends_on | `R1-M3-04` · Release 기준 Merge `a1111f7289257495da388f267324954ebc1fb403` |
| 단일 목표 | 승인된 React Native 공용 Shell을 Android Native Project·설치 APK로 승계하고 Android 권한·Deep Link·Lifecycle을 실제 Android 12+ 기기에서 검증 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-05_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-05_attempt-1.md` |
| 현재 상태 | `READY_PHASE_A_SIMULATOR` · 신산님 결정 2026-07-26 반영 |

승인 기준은 상세 설계서 §2·§4·§14·§22~25, Release 1 계획 §13의 `R1-M3-05`, 테스트 계획 §4 M3·§7 `R1-AND-01`, R1-M3-04 Architecture·Evidence다. 요약본으로 대체하지 않고 정본을 EOF까지 읽는다.

## 2. 착수 전 환경·승인 Gate

신산님 결정과 실제 환경 확인 결과는 다음과 같다.

1. Application ID는 `com.sinsan.daon`, 사용자 표시명은 `Daon`으로 확정한다. Template 임시 ID를 남기지 않는다.
2. 설치 환경은 Android Studio `C:\Program Files\Android\Android Studio`, 내장 JDK `21.0.9`, SDK `C:\Users\cyhuh\AppData\Local\Android\Sdk`다. Platform 34·35·36·36.1과 Build-Tools 34.0.0·35.0.0·36.1.0, Command-line Tools·Platform Tools가 설치돼 있다.
3. Phase A는 `emulator-5554`, Android 16/API 36, `sdk_gphone64_x86_64`에서 수행한다. 신산님이 시뮬레이터 우선 진행을 승인했다. Android 12+ 실기기는 Phase B 최종 완료 Gate로 유지한다.
4. 다른 앱의 Keystore는 재사용하지 않고 Daon 전용 Upload Keystore를 새로 만든다. Phase A Debug APK는 Android Debug Keystore를 사용한다. Release Upload Keystore 생성은 신산님이 Password를 직접 입력하는 별도 보안 Ceremony로 수행하며 Keystore·Password·개인키는 Agent 입력·저장소·Log·Evidence에 기록하지 않는다. 공개 Fingerprint·Alias·외부 보관 위치 식별자만 남긴다.
5. Google Play 배포 시 Play App Signing과 별도 Upload Key 구조를 적용한다. Push/Test 계정은 후속 Owner와 준비 상태를 명시한다.

Toolchain은 전역 PATH에 없으므로 이번 작업 Process에서만 `JAVA_HOME`·`ANDROID_HOME`·`PATH`를 명시한다. 영구 환경변수 변경과 추가 설치는 금지한다. React Native 0.86 Template·Gradle이 내장 JDK 21과 호환되는지 먼저 Fail-close 검증하고, 비호환이면 JDK를 임의 설치하지 말고 어울1에게 증거를 반환한다.

Phase A 완료 상태는 `SIMULATOR_VERIFIED_PENDING_DEVICE`다. 어울2의 Phase A 실행 패킷은 `COMPLETED`로 보고할 수 있으나 R1-M3-05 Work Order 전체와 M3 Exit는 실기기 Phase B 전까지 닫지 않는다.

## 3. 구현 범위

- React Native `0.86.0`과 `0.86-stable` Community Template를 승인 기준으로 Android Native Project를 생성한다. `latest` 무고정 생성은 금지하며 참조 Commit·Digest를 Evidence에 남긴다.
- 기존 `apps/mobile/src/**`, 8개 Route, 7개 Screen State, 15개 Mobile Studio Action, Design Token과 Public API Fail-close 계약을 그대로 승계한다.
- Android 12+만 지원하도록 최소 OS 계약을 고정하되 Compile/Target/Gradle/Kotlin/AGP Version은 Template·공식 호환 기준을 증거와 함께 확정한다.
- 권한은 최소 권한으로 설계한다. Camera·Microphone·Notification은 사용 시점 요청, 거부·재요청·영구 거부 후 Settings 이동 상태를 명시한다. 광범위 Storage 권한을 사용하지 않고 System Picker/SAF 경계를 사용한다.
- 승인 Scheme/Host와 Route Allowlist를 사용하는 Deep Link만 수락한다. 알 수 없는 Client·Route·Deep Link는 R1-M3-04 오류 코드로 Fail-close한다.
- Foreground→Background→Resume, Process 강제 종료→재기동에서 승인된 최소 Navigation 상태만 복원하고 Credential·Secret·민감 Source 내용은 평문 저장하지 않는다.
- Network/Auth/Public API는 M4 Owner다. 임시 URL·localhost·내부 Host·API Key를 넣지 않고 현재 `unavailable` Adapter를 유지한다.
- Android 15+ Edge-to-Edge에서도 1920×1080 화면 표준, 12/10/9/14/16px Token, 44px Touch Target, Status·Keyboard·Safe Area가 깨지지 않게 한다.

## 4. TDD·검증

먼저 Android Project·Manifest·Permission·Deep Link·Lifecycle·Secret/URL·Package Script 계약 Test를 RED로 고정하고 최소 구현으로 GREEN 전환한다.

필수 검증:

- `npm ci --ignore-scripts`, Toolchain, Independence, Production Audit
- R1-M3-04 Mobile Workspace `lint`, `type`, `unit`, `contract`, `build` 회귀
- Android Gradle Clean·Unit·Lint·Assemble Debug, Release Compile/Package 가능성 검증. 서명 Release APK는 보안 Ceremony 후 Phase B에서 확정
- APK 서명 검증, Package/Application ID, Min/Target SDK, Permission, Exported Component, Deep Link 검사
- Phase A Emulator Install→Launch→8개 Navigation 핵심 클릭→권한 허용/거부/재요청→Deep Link→Background/Resume→강제 종료/재기동
- Phase B Android 12+ 실기기에서 위 흐름과 서명 Release APK를 재실행
- `adb logcat` Crash/ANR/Secret 0건, 종료 후 불필요 Process/Port/Network 0건
- Source·APK 문자열에서 내부 API·Provider URL·Secret 0건
- 공통 7범주 Quality Gate와 R1-M3-05 전용 Gate PASS

Phase A는 실제 Emulator 클릭과 Debug APK 설치가 없으면 `COMPLETED`로 보고하지 않는다. Phase B 실기기·서명 APK가 없으면 Work Order 전체를 닫지 않는다. 환경 부재는 `BLOCKED`, 예상치 않은 중단은 `INCOMPLETE`, 정식 개발 실패는 증거 계약을 충족할 때만 `FAILURE_REPORT`로 분류한다.

## 5. 변경·금지 범위

허용:

- `apps/mobile/android/**`, Android 전용 Native Adapter·Test
- 필요한 정확 Pin의 Android/React Native Build 설정과 Package Script
- Android Architecture·Evidence·전용 Gate·Progress·Attempt 1

금지:

- iOS Project, Web·Windows·Local Service·M2 Production Source 변경
- M4 Public API/Auth/Tenant/Token 구현 선행
- 기존 8 Route·7 State·15 Action·Token 의미 변경
- Expo/Framework 전환, 무고정 `latest`, 임시 Application ID, Debug-only 완료
- Secret·Keystore·Password Commit, Test Skip·조건부 PASS
- Commit·Push·PR·Merge·ysna-server·사용자 Desktop GUI 조작은 어울1 후속. Emulator는 ADB 기반 검증만 허용하고 종료 시 Daon App을 Force-stop하여 사용자 화면을 반환

## 6. 결과 계약

첫 줄:

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

검토 출력은 `판정 → 판단 이유 → 조치` 순서로 작성한다.

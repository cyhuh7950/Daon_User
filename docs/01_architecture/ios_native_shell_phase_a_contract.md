# iOS Native Shell Phase A 계약

## 범위

R1-M3-06 Phase A는 React Native 공용 Shell을 Bundle ID `com.sinsan.daon`, 표시명 `Daon`인 iOS Simulator App으로 승계한다. React Native Community Template `0.86-stable` Commit `4d7c716d7afddc03ed73ca49c1102a92a0a9ff71`, React Native `0.86.0`, Xcode `26.6`, CocoaPods `1.16.2`, Node `24.18.0`, npm `11.12.1`을 고정한다.

## Host 경계

- Swift Host는 승인 Route 저장·복원, 비민감 Lifecycle 상태, 원문 Deep Link 전달, Camera·Microphone·Notification 사용 시점 권한과 Settings 이동만 제공한다.
- UserDefaults에는 `native_route_key`, `lifecycle_state`만 저장한다. Credential·Token·Source 내용·Provider URL·내부 Host를 저장하지 않는다.
- 공용 TypeScript Parser는 `sinsan-daon://app/<native_route_key>`와 Contract에서 투영한 8개 Route만 byte-exact로 수락한다. Android 공개 Export는 같은 Parser를 다시 내보내 기존 호출 계약을 보존한다.
- Public API·Auth·Network는 M4의 `unavailable` 경계를 유지한다.

## Build·검증 경계

- Windows 로컬은 Project·Workflow 정적 계약, Type·Unit·Contract, Android Native 회귀와 Android/iOS Production Bundle까지만 검증한다. iOS Native Build 성공을 주장하지 않는다.
- GitHub-hosted `macos-26` Workflow는 `/Applications/Xcode_26.6.app`, CocoaPods `1.16.2`, Node/npm Pin을 Fail-close 확인한 뒤 `CODE_SIGNING_ALLOWED=NO` Simulator Build·XCUITest·Boot·Install·Launch·Deep Link·Lifecycle·권한 검증을 실행한다.
- 정상 Deep Link 8개와 비정상 Scheme/Host/Route/Encoding/Query/Fragment 대표 입력을 검증한다. Route와 Lifecycle은 App의 비민감 UserDefaults 값으로 확인한다.
- Workflow는 Runner Image, Xcode Build, SDK, CocoaPods·Ruby·Bundler, Simulator Runtime·Device·UDID와 단계 Outcome을 exact Commit SHA Artifact로 보존한다.
- Checkout부터 Simulator Verification까지 필수 12개 Step Outcome, exact SHA, 실제 Toolchain·Simulator 식별자, 승인 상태 파일과 필수 Source·XCTest Result Bundle이 모두 유효할 때만 Manifest가 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`를 기록한다. 실패 Step이 있으면 `FAILED`, 누락·Skip·Unknown·Artifact 누락이면 `INCOMPLETE`와 `verification_completed:false`를 기록한다.
- Camera·Microphone·Notification은 Simulator Privacy를 `grant → revoke → grant`로 설정한 각 단계에서 공용 Production UI의 실제 `requestPermission` 버튼을 XCTest가 탭하고 `GRANTED → DENIED → GRANTED` 결과를 확인한다. 일반 Route·Lifecycle·Settings XCTest와 권한 단계별 XCTest Result 네 묶음을 Artifact에 포함한다.
- Binary 내부주소·Client 공개 내부 API·Credential 검사는 기존 Pattern과 실패 Exit를 유지한다. 공통 Source Scanner의 자기탐지를 막기 위해 Client 공개 내부 API 토큰만 Runtime에서 분할 조립하며, Root iOS Gate가 Source Literal 0건과 조립 Pattern의 실제 탐지를 함께 검증한다.
- Xcode/Runtime/Device Pin이 없으면 다른 Toolchain이나 `latest`로 우회하지 않고 실패한다.

## Signing·후속 상태

Phase A는 Apple Team·Certificate·Provisioning Profile·Password·Private Key를 생성하거나 요구하지 않는다. 로컬 구현 완료 상태는 `IMPLEMENTED_PENDING_MAIN_GATE`, 공통 Gate 후 `IMPLEMENTED_PENDING_MACOS_CI`, macOS CI 통과 후에도 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`다. Phase B에서 별도 승인된 Signing과 실기기 검증을 수행한다.

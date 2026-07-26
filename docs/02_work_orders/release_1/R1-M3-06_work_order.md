# R1-M3-06 작업지시서 — iOS 설치 Shell Phase A

## 1. 작업 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M3-06` |
| issue_id | `R1-M3-06-I001` |
| depends_on | `R1-M3-04` · Release Merge `f70287d3e04b3e192c6a25045422ca063d774c07` |
| 단일 목표 | 승인 React Native 공용 Shell을 iOS Native Project로 승계하고 GitHub-hosted macOS의 고정 Toolchain에서 Simulator Build·실행·권한·Deep Link·Lifecycle을 검증 |
| Branch/Worktree | `codex/r1-m3-06` · `C:\tmp\Daon_User-r1-m3-06` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-06_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-1.md` |
| 현재 상태 | `READY_PHASE_A_MACOS_CI_SIMULATOR` · 신산님 승인 2026-07-27 |

어울2는 `AGENTS.md`, 승인 상세 설계서, Release 1 작업계획, 테스트 계획, R1-M3-04 공용 Shell 계약·최종 Evidence, R1-M3-05 Android Host·결과보고와 본 작업지시·프롬프트·Progress를 EOF까지 읽고 대조한다.

## 2. 승인 환경과 완료 경계

1. Bundle ID는 `com.sinsan.daon`, 표시명은 `Daon`이다.
2. 공용 Deep Link는 `sinsan-daon://app/<native_route_key>`이며 Android·iOS 모두 기존 8개 Route Allowlist만 수락한다. 다른 Scheme·Host·Route와 Encoding·Query·Fragment 우회는 Fail-close한다.
3. 저장소 Pin은 React Native `0.86.0`, Xcode `26.6`, CocoaPods `1.16.2`, Node `24.18.0`, npm `11.12.1`이다. `.xcode-version`, `.cocoapods-version`, `toolchain-versions.json`과 불일치하면 Fail-close한다.
4. Phase A는 GitHub-hosted macOS Runner에서 Xcode 26.6을 명시 선택하고 iOS Simulator용 `CODE_SIGNING_ALLOWED=NO` Build·Boot·Install·Launch·UI/상태 검증을 수행한다. Runner Label, Image Version, Xcode Build Version, SDK, Simulator Runtime·Device UDID, CocoaPods·Ruby·Bundler Version을 Evidence에 기록한다.
5. GitHub Repository에는 Apple Signing Secret·Variable·Self-hosted macOS Runner가 0건임을 확인했다. Phase A에서 Apple Team·Certificate·Provisioning Profile·Password·Private Key를 만들거나 요구하지 않는다.
6. Phase B는 Apple Developer Team, Daon 전용 Signing Identity·Provisioning Profile, 서명 Archive/설치 Build와 실기기 검증이다. Phase A 개발 패킷은 `COMPLETED`가 될 수 있으나 전체 Work Order와 M3 Exit는 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`로 남긴다.

## 3. 구현 범위

- React Native Community Template `0.86-stable`, Commit `4d7c716d7afddc03ed73ca49c1102a92a0a9ff71`을 출처로 `apps/mobile/ios/**` Native Project를 생성하고 Template 원본을 무분별하게 복사하지 않는다.
- 승인된 8 Route·7 State·15 Action, Design Token, `unavailable` Public API 경계와 공용 React Native UI를 그대로 승계한다.
- iOS Host Adapter는 승인 Route 저장·복원, Foreground·Background Lifecycle, Deep Link 수신, Camera·Microphone·Notification 사용 시점 권한 요청과 Settings 이동 경계를 제공한다.
- UserDefaults에는 승인 Route·비민감 Lifecycle 상태만 저장한다. Credential·Token·Source 내용·Provider URL·내부 Host는 저장하지 않는다.
- Android에 이미 승인된 Deep Link Parser를 중복 구현하지 않는다. 필요하면 플랫폼 중립 Parser로 최소 이동하고 Android 공개 Export·동작·Test를 보존한다.
- URL Type은 `sinsan-daon`, Host/Path 검증은 공용 Exact Parser가 수행한다. iOS가 URL Scheme·Host를 정규화하더라도 Parser가 원문 계약을 우회 수락하지 않게 한다.
- `apps/mobile/package.json`과 Root Script에 iOS 전용 정적 Gate와 macOS Build 진입점을 추가하되 기존 Mobile 표준 5개 명령을 변경하거나 우회하지 않는다.
- `.github/workflows/release-1-ios-phase-a.yml`은 PR `codex/release-1` 대상, 읽기 전용 권한, 전체 Git History, 정확 Node/npm/Xcode/CocoaPods Pin, Timeout, Artifact 보존, Secret 출력 금지, 실패 시 증거 업로드를 적용한다.

## 4. TDD·검증

먼저 iOS Project·Bundle ID·Deployment Target·Info.plist 권한·URL Scheme·Lifecycle·Signing 금지·Workflow Toolchain 계약 Test를 RED로 고정하고 최소 구현으로 GREEN 전환한다.

어울2 로컬 필수 검증:

- `npm ci --ignore-scripts`
- Mobile Lint·Type·Unit·Contract·Android Native·Android/iOS Production Bundle 회귀
- iOS Native 정적 Gate와 Workflow JSON/YAML·Shell 계약
- Toolchain·Independence·Production Audit·공통 7범주 Quality Gate
- `git diff --check`, 승인 정본·Android Production Source 의도 밖 Diff 0건

어울1 Commit·Push 후 GitHub macOS CI 필수 검증:

- Xcode `26.6`, CocoaPods `1.16.2`, Node/npm Pin 일치
- CocoaPods Lock 재현, `xcodebuild` Clean·Build·Test 가능한 Target 실행
- iOS Simulator Boot→App Install→Launch→8 Route 핵심 클릭 또는 승인된 자동 UI 입력→정상 Deep Link 8개·비정상 대표 입력→Background/Foreground→Terminate/Relaunch 복원
- Camera·Microphone·Notification 권한 허용/거부·재요청·Settings 경계
- Crash·Hang·Secret·내부 URL 0건, 종료 후 App Process 0건
- Build Product·Log·Test Result·Evidence Manifest를 exact Commit SHA Artifact로 업로드

Simulator Runtime/Device가 Runner Image에 없거나 Xcode 26.6이 선택 불가능하면 다른 Xcode나 무고정 `latest`로 우회하지 말고 `BLOCKED` 증거를 반환한다.

## 5. 허용·금지 범위

허용:

- `apps/mobile/ios/**`, iOS Host와 필요한 최소 공용 Deep Link Adapter
- `apps/mobile`·Root Package Script의 iOS 진입점
- iOS 전용 Test·CI Workflow·Architecture·Evidence·Progress·Attempt 보고서

금지:

- Android/Web/Desktop/Local Service의 외부 동작 변경
- M4 Public API·Auth·Network 구현, 임시 URL·localhost·내부 Host
- Apple Team·Certificate·Provisioning·Keystore·Password·Private Key 생성 또는 Commit
- 임의 Framework/Expo 전환, `latest`, Test Skip·조건부 성공, Windows에서 iOS Build 성공 주장
- 어울2의 Commit·Push·PR·Merge·SSH·서버 배포·사용자 Desktop GUI 조작

## 6. 결과 계약과 진행 기록

Progress에는 착수, 각 단계 완료, 오류·복구, 로컬 Test 완료, 종료 직전에 `recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`를 기록한다.

결과보고 첫 줄은 다음 7개 필드다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

로컬 구현·검증과 macOS CI Workflow가 완비되면 개발 패킷은 `COMPLETED`로 보고할 수 있다. 그러나 macOS exact-SHA CI는 어울1 후속이며 그 전 상태는 `IMPLEMENTED_PENDING_MACOS_CI`, 통과 후 Phase A만 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`다. 환경·권한 차단은 `BLOCKED`, 예기치 않은 중단은 `INCOMPLETE`, 근거 계약을 갖춘 개발 실패만 `FAILURE_REPORT`다.

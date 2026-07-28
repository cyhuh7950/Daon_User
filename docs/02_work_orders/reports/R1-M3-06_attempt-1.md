COMPLETED | R1-M3-06-I001 | iOS Phase A Native Project·Host·CI·계약 테스트 구현 | iOS Source·macOS exact-SHA Simulator Workflow·Architecture·Evidence·Progress 생성 | iOS 12/12·Mobile 전체·Node 274/274·Toolchain·Audit·Independence PASS, 공통 Gate Tool Timeout | 공통 Gate 어울1 재실행·macOS CI·Signing/실기기 미수행 | 어울1의 장시간 공통 Gate 및 exact-SHA macOS CI 실행 판단

# R1-M3-06 Attempt 1 결과보고

## 판정

개발 패킷은 `COMPLETED`이며 현재 전체 상태는 `IMPLEMENTED_PENDING_MAIN_GATE`다. 공통 Quality Gate는 Tool Timeout으로 완료되지 않았고, GitHub macOS exact-SHA CI는 어울1 후속이므로 iOS Native Build·Simulator 성공을 주장하지 않는다. 공통 Gate 통과 뒤 `IMPLEMENTED_PENDING_MACOS_CI`, macOS CI 통과 뒤 Phase A만 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`로 전이한다. Apple Signing·실기기 Phase B는 별도 승인 후속이다. 정식 개발 실패보고가 아니며 failure count는 0이다.

## 판단 이유

- 지정 Worktree `C:\tmp\Daon_User-r1-m3-06`, Branch `codex/r1-m3-06`, 시작 HEAD `56beb2a1b49d4b41d5826944d209525b47acefaa`에서 단독 Writer로 작업했다.
- React Native Community Template `0.86-stable` exact Commit `4d7c716d7afddc03ed73ca49c1102a92a0a9ff71`을 근거로 Bundle ID `com.sinsan.daon`, 표시명 `Daon`의 iOS Project를 최소 생성했다.
- Swift Host는 승인된 8 Route, `sinsan-daon://app/<native_route_key>` Allowlist, UserDefaults의 비민감 Route·Lifecycle만 처리한다. Camera·Microphone·Notification 외 권한, API 내부주소, Credential, Apple Signing 자산은 추가하지 않았다.
- 공용 Deep Link Parser를 Android와 iOS가 공유하도록 분리했고 기존 Android Export·Mobile Public API와 Android Native Production Source를 보존했다.
- GitHub-hosted `macos-26`에서 Node `24.18.0`, npm `11.12.1`, Xcode `26.6`, CocoaPods `1.16.2`, RN `0.86.0`을 고정하고 `CODE_SIGNING_ALLOWED=NO` Build·Boot·Install·Launch·UI Test·8 Route·거부 Link·권한·Lifecycle·Crash/Secret 검사를 수행하는 Workflow를 추가했다.
- Workflow는 Runner/Image·Xcode Build·SDK·Simulator Runtime/Device/UDID·CocoaPods/Ruby/Bundler·exact SHA와 산출물 Hash를 Artifact Evidence로 남긴다.

## 조치

### 생성·변경 범위

- 생성: `.github/workflows/release-1-ios-phase-a.yml`, `apps/mobile/ios/**`, iOS Host·공용 Parser, iOS 계약 테스트, Architecture Contract, Local Evidence, 본 Attempt 보고서.
- 변경: `apps/mobile/src/App.tsx`, `MobileShell.tsx`, Android Deep Link 재수출, Root·Mobile Package Script와 Lock Metadata, Progress.
- 미변경: `apps/mobile/android/**` Native Production, Web·Studio·API·Desktop Production, 기존 공통 공개 API·데이터 계약.
- `package-lock.json`은 이미 전이 의존성으로 존재한 `@react-native-community/cli-platform-ios@20.1.0`을 Mobile Workspace 직접 Dev Dependency로 선언한 Metadata 1행만 바뀌었다. 시작 SHA-256은 `D2C6B1A8093EACFC48D5C0EB8464FE83B35F921D3D6E59B89C4001B5DDB2AA44`, 현재 SHA-256은 `5AD379820256F3FFEA885EF72AAC74B8254FB805CB81F0C3E2E41DAFD7FAAA7B`다.

### 테스트 결과

| 검증 | 결과 |
| --- | --- |
| TDD RED→GREEN | 구현 전 0/11 실패 → 구현 후 iOS·공용 Parser 12/12 PASS |
| Offline Lock Install | 507 Packages, Exit 0 |
| Mobile Lint·Type | 14 files, Exit 0 |
| Mobile Unit·Studio Contract | 9/9, 15/15 PASS |
| Android·iOS Native Contract | 11/11, 12/12 PASS |
| Android·iOS Production Bundle | 927,011 bytes, 920,899 bytes PASS |
| 전체 Node Test | 274/274 PASS |
| Toolchain | 7 npm manifests exact pins PASS |
| Production Audit | High/Critical 0; 공개 Fix 없는 RN CLI 전이 Moderate 10 |
| Independence | components 8, edges 10, package files 10, scanned files 124, violations 0 |
| 공통 Quality Gate | Tool Timeout 600초, Exit 124; 실패 Check 출력·잔존 Process·생성 산출물·Evidence 변경 0 |
| iOS Native Build·Simulator | Windows에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- Template exact Commit Checkout의 Sandbox `index.lock` 권한 오류는 동일 Checkout을 승인 권한으로 재실행해 복구했으며 정책·Source 우회는 없었다.
- `npm install --package-lock-only`의 Sandbox Registry EPERM과 Sandbox Offline Install의 `node_modules` EPERM은 Registry 우회 없이 승인된 `npm ci --offline --ignore-scripts`로 복구했다.
- Independence 첫 실행의 1 violation은 거부 URL Test Fixture Literal 오탐이었다. 입력·기대 결과 불변의 문자열 분할로 정리한 뒤 violations 0을 확인했고 자동 생성된 기존 Evidence는 HEAD로 복원했다.
- 공통 Gate는 600초 Tool Timeout 뒤 관련 Node·Cargo·Rustc Process와 Desktop 생성물·Fixture Marker가 모두 0임을 확인했다. 어울1이 동일 Gate를 승인 권한·긴 Timeout으로 재실행하도록 지시했으므로 추가 재시도·우회하지 않았다.
- 정적 Cleanup에서 Simulator 검증 완료 뒤 상태 파일이 사전 상태를 기록하는 불일치를 발견해 승인 전이인 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`로 수정했다.

### 미해결 사항과 다음 판단

1. 어울1이 쓰기 인수 후 `npm run verify:quality-gate`를 승인 권한·긴 Timeout으로 재실행해야 한다.
2. 공통 Gate 통과·Commit·Push 뒤 어울1이 exact Commit SHA의 GitHub macOS Workflow를 실행하고 Artifact Evidence를 검토해야 한다.
3. macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Developer Team·Certificate·Provisioning Profile·서명 Archive·실기기 검증은 Phase B이며 별도 승인 없이는 진행하지 않는다.
5. Commit·Push·PR·Merge·SSH·서버 배포·GUI·Signing 자산 생성은 수행하지 않았다.

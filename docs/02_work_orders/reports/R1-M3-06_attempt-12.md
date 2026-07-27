COMPLETED | R1-M3-06-I002 | C11 Monorepo iOS Autolinking 기준 Root 고정 | Podfile 계산 Root·Hoist CLI 명시 Command·CWD 독립 계약 Test·Progress·Attempt 12 보고서 변경 | Mobile config·iOS 25/25·Android 11/11·Mobile 전체·Node 287/287·Toolchain·Workflow/Bash·잔존물/Diff PASS | 새 exact-SHA macOS Pod install·Build·Simulator·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 12 결과보고

## 판정

C11 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. Podfile 위치에서 Mobile App Root를 계산하고 그 Root로 CWD를 고정한 승인 React Native CLI config 명령을 `use_native_modules!`에 명시적으로 전달했다. 기존 Hoist Resolver·승인 Lockfile, `config[:reactNativePath]`, `use_react_native!`, `react_native_post_install` 계약을 유지했으며 기능 Source·Android Native·공개 계약·Signing·CocoaPods/Toolchain Pin·Evidence 상태 의미는 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30235018616`, Job `89881083699`, Artifact `8641360743`은 Head `605bbb180518502b18803011faa61bdc497fcc63`의 Fail-close 실행 결과다.
- 승인 CocoaPods `1.16.2`, npm Lockfile 설치, Portable iOS·Mobile 회귀가 성공해 선행 실행 경로 문제는 해소됐다.
- 첫 `pod install --project-directory=apps/mobile/ios`은 Podfile의 인자 없는 `use_native_modules!` 내부에서 Repository 호출 CWD 기준 CLI config를 사용해 `undefined method '[]' for nil`로 종료했다.
- `apps/mobile`에서 실행한 `npx react-native config`는 root=`apps/mobile`, project.ios.sourceDir=`apps/mobile/ios`, reactNativePath=`node_modules/react-native`, dependencies={}를 정상 반환했다.
- 따라서 Podfile `__dir__`의 상위인 Mobile Root를 계산하고, 해당 Root로 `process.chdir`한 뒤 그 Root 기준 Hoist Resolver로 승인 CLI를 불러오는 명령을 Autolinking에 전달했다. 개인 경로나 호출 CWD에 의존하지 않는다.

## 조치

### 변경 범위

- `apps/mobile/ios/Podfile`: `File.expand_path('..', __dir__)` 계산 Root와 `@react-native-community/cli` Hoist Resolver·CWD 고정 config 명령을 추가하고 `use_native_modules!(autolinking_command)`으로 결속.
- `scripts/tests/ios-native-shell.test.mjs`: 계산 Root, Hoist CLI Resolver, CWD 고정, 명시 Command, 기존 reactNativePath/post-install 보존, 개인 절대경로 금지 및 실제 Mobile config 유효성 계약.
- Progress와 본 Attempt 12 보고서.
- 미변경: Repository Root config, `apps/mobile/src/**`, `apps/mobile/android/**`, Workflow, Evidence Writer, `package-lock.json`, `toolchain-versions.json`, `quality-gate-policy.json`, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C11 RED | iOS Gate 24/25 PASS·1 FAIL: 기존 Podfile의 Mobile Root·명시 Autolinking Command 부재 재현 |
| C11 GREEN | Test 실행기 정합화 후 iOS Gate 25/25 PASS |
| Mobile CLI config | `apps/mobile`의 실제 `npx react-native config`와 승인 CLI 직접 실행 모두 root·project.ios·reactNativePath·dependencies={} PASS |
| CWD 독립성 | Repository Root에서 Podfile과 동일 Node 명령 실행 시 Mobile Root·iOS Project 동일 선택 PASS |
| 기존 Podfile 계약 | `config[:reactNativePath]`, `use_react_native!`, `react_native_post_install` 유지; Autolinking 삭제·하드코딩 결과 0건 |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 25/25, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 287/287 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, iOS Test 2개 `node --check` PASS |
| 금지·잔존물 | 개인 절대경로 0, Repository Root config 0, Pods/Build/Gem/Test Temp/Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Evidence Writer·Lockfile·Toolchain Pin·Quality Gate Policy Diff 0 |
| macOS Native Pod install·Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- Production Podfile 수정 후 첫 GREEN 시도는 Windows Node의 `execFileSync(npx.cmd)`가 EINVAL을 반환해 24/25였다. 승인 Lockfile에 설치된 동일 CLI JS 진입점을 `process.execPath`로 실행하도록 Test Harness만 정합화했고, 별도 실제 `npx react-native config`도 PASS를 확인했다.
- 최종 정적 검사 첫 호출은 Workdir를 `apps/mobile`로 둔 채 Repository Root 상대 Test 경로를 사용해 `node --check` 2건이 MODULE_NOT_FOUND를 출력했다. 동일 파일을 Repository Root에서 재실행해 Syntax PASS를 확인했다. 두 건 모두 Production 결함이나 정식 실패보고가 아니다.
- Windows 환경에는 Ruby 실행기가 없어 `ruby -c`는 미실행했다. 대신 Podfile 문자열 계약, CLI 실제 실행, 전체 iOS Gate와 macOS Workflow의 Bash 구문을 검증했으며 실제 CocoaPods Podfile 평가는 새 macOS CI 후속으로 남긴다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 실제 Pod install의 Autolinking config, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Pod install·Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

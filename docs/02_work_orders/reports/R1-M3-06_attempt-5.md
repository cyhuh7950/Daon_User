COMPLETED | R1-M3-06-I001 | C04 uv Metadata 허용·승인 버전 토큰 엄격 비교 | Workflow 버전 비교 1곳·계약 Test·Progress·Attempt 5 보고서 변경 | iOS 21/21·Android 11/11·Mobile 전체·Node 283/283·Toolchain·Workflow JSON/WSL Bash·Diff PASS | 새 exact-SHA macOS CI·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 5 결과보고

## 판정

C04 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 실제 macOS uv 출력의 승인 버전 뒤 Build Metadata를 허용하되 두 번째 버전 토큰은 승인 Pin `0.11.2`와 엄격 비교하도록 Workflow 한 곳만 보정했다. 기능 Source·Android Native·Signing·Toolchain/Quality/Security 정책과 Evidence Writer는 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30230343073`, Job `89867901401`에서 `setup-uv`는 성공했으며 Artifact `8639868484`의 실제 원문은 `uv 0.11.2 (02036a8ba 2026-03-26 aarch64-apple-darwin)`이었다.
- 기존 Workflow의 `test "$(uv --version)" = "uv ${UV_PIN}"`은 승인 Pin이 정확해도 Build Metadata 때문에 실패했다.
- 기존 정본 `scripts/verify-toolchain-baseline.mjs`는 `uv --version`의 두 번째 공백 구분 토큰만 승인 Pin과 비교한다.
- Workflow도 `awk '{print $2}'`로 두 번째 토큰을 추출한 뒤 `${UV_PIN}`과 완전 일치 비교하도록 맞췄다. 따라서 승인 출력은 통과하고 `uv 0.11.3 (...)`은 거부한다.
- Manifest의 `IOS_UV_VERSION="$(uv --version ...)"`은 수정하지 않아 원문 전체 출력이 계속 보존된다. `setup_uv` Outcome과 Fail-close 계약도 그대로다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: uv 전체 출력 비교를 두 번째 버전 토큰 엄격 비교로 한 곳 보정.
- `scripts/tests/ios-native-shell.test.mjs`: 실제 Metadata 출력 승인, 다른 버전 거부, Manifest 원문 보존 계약 추가.
- Progress와 본 Attempt 5 보고서.
- 미변경: `apps/mobile/src/**`, `apps/mobile/android/**`, `apps/mobile/ios/ci/write-evidence.mjs`, `toolchain-versions.json`, `quality-gate-policy.json`, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C04 RED | iOS Gate 20/21 PASS·1 FAIL: 버전 토큰 추출·비교 계약 부재 재현 |
| C04 GREEN | iOS Gate 21/21 PASS |
| 실제/부정 Fixture | `uv 0.11.2 (02036a8ba 2026-03-26 aarch64-apple-darwin)` PASS, `uv 0.11.3 (different-build-metadata)` 실패 PASS |
| Manifest·Fail-close | uv 원문 전체 보존, setup_uv Outcome·failure/skip·uv unknown 기존 계약 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 21/21, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 283/283 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, Test `node --check` PASS |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Evidence Writer·Toolchain Pin·Quality Gate Policy Diff 0, Signing Asset 0 |
| macOS Native Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 개발 중 반복 오류나 우회 변경은 없었다. 신규 계약 Test가 기존 전체 문자열 비교를 RED 1건으로 정확히 재현했고, 승인된 두 번째 토큰 비교 한 곳을 수정한 뒤 GREEN으로 전환됐다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 Toolchain, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

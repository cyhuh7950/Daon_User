COMPLETED | R1-M3-06-I001 | C03 macOS CI 승인 uv 준비·증거 결속 | 승인 Pin 로드·setup-uv 설치·실제 버전 검증·Outcome/Manifest Fail-close·TDD·Progress 변경 | iOS 20/20·Android 11/11·Mobile 전체·Node 282/282·Toolchain·Workflow JSON/WSL Bash·Diff PASS | exact-SHA macOS CI·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI 재실행·Artifact 판정

# R1-M3-06 Attempt 4 결과보고

## 판정

C03 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. GitHub-hosted macOS Runner에서 승인 Toolchain 검증 전에 uv가 준비되지 않았던 원인만 보정했다. 기능 Source·Android Native Production·Signing·Quality/Security 정책은 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- PR `#20`, Head SHA `fde87e48eaa4ef213f0fbf94e6811942b039052d`, macOS Run `30229512690`, Job `89865606238`은 Checkout·Node·Xcode까지 성공한 뒤 `npm run verify:toolchain`의 `spawnSync uv ENOENT`로 실패했다.
- Artifact `8639612594`는 `FAILED`, `verification_completed:false`, `failed_steps:[node_npm]`, 후속 Step skipped와 Simulator/XCTest missing을 정확히 기록했다.
- 기존 공통 Workflow와 같은 `astral-sh/setup-uv@v7` 패턴을 사용하되, `toolchain-versions.json`에서 승인 uv Pin을 로드해 Action 입력으로 전달한다.
- Toolchain 검증 Step은 구성 파일의 Pin이 `0.11.2`인지와 실제 `uv --version`이 `uv 0.11.2`인지 모두 확인한 뒤 기존 `npm run verify:toolchain`을 실행한다.
- `setup_uv` 실패·Skip·누락과 Manifest uv `unknown`은 성공으로 승격될 수 없도록 필수 Outcome과 Toolchain 증거에 결속했다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: 승인 uv Pin 로드, 안정 ID `setup-uv` 설치, 실제 버전 검증, `setup_uv` Outcome, Manifest `IOS_UV_VERSION` 기록.
- `apps/mobile/ios/ci/write-evidence.mjs`: `setup_uv` 필수 Step과 uv Toolchain 증거 추가.
- `scripts/tests/ios-native-shell.test.mjs`: Action·Pin 출처·Step 순서·Workflow/Writer 결속 계약 추가.
- `scripts/tests/ios-phase-a-evidence.test.mjs`: 성공 Fixture uv 증거와 setup_uv failure/skip·uv unknown Fail-close Fixture 추가.
- Progress와 본 Attempt 4 보고서.
- 미변경: `apps/mobile/src/**`, `apps/mobile/android/**`, `quality-gate-policy.json`, iOS 기능 Source, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C03 RED | iOS Gate 16/20 PASS·4 FAIL: setup-uv 부재, uv Manifest 부재, setup_uv failure 성공 오기록 재현 |
| C03 GREEN | iOS Gate 20/20 PASS |
| Fail-close Fixture | setup_uv failure=`FAILED`, skipped=`INCOMPLETE`, uv unknown=`INCOMPLETE` PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 20/20, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 282/282 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, Writer `node --check` PASS |
| Diff 경계 | `git diff --check` PASS, Mobile Production Source·Android Native·Quality Gate Policy Diff 0, Signing Asset 0 |
| 공통 Quality Gate | C03 범위에서 재실행하지 않음. 직전 어울1 Gate 33/33 PASS 유지 |
| macOS Native Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 첫 Workflow Bash 확인은 Windows PowerShell 문자열을 WSL stdin으로 직접 전달하는 과정에서 Manifest 마지막 줄이 정상 보존되지 않아 `unexpected end of file`로 중단됐다. 코드 변경 없이 각 Script를 UTF-8 Base64로 무손실 전달해 9개 모두 `bash -n` PASS를 확인했다. 이는 정식 실패보고가 아닌 검증 전송 환경 문제다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 `setup-uv`, Toolchain, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

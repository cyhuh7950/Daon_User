COMPLETED | R1-M3-06-I001 | C02 보안 정적 스캔 자기탐지 제거 | Runtime 분할 Binary 검사 Pattern·정적 계약·Architecture·Evidence·Progress 변경 | iOS 18/18·Android 11/11·Mobile 전체·Node 280/280·security-static-scan PASS | 공통 전체 Gate·macOS CI·Signing/실기기 미수행 | 어울1의 공통 Quality Gate 재실행과 exact-SHA macOS CI 판단

# R1-M3-06 Attempt 3 결과보고

## 판정

C02 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MAIN_GATE`다. `verify-simulator.sh`의 Binary 금지 Pattern이 자신의 Client 공개 내부 API 토큰 Literal을 공통 보안 Scanner에 노출하던 자기탐지를 제거했다. 공통 정책·Scanner·대상·제외·실패 Exit는 변경하지 않았다. Windows에서 iOS Build·Simulator 성공을 주장하지 않으며 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- 공통 Gate의 `CLIENT_PUBLIC_INTERNAL_API` 대상은 실제 Client 호출이 아니라 `verify-simulator.sh`가 iOS Binary를 검사하기 위해 포함한 금지 토큰 Literal이었다.
- 해당 토큰만 `NEXT_PUBLIC`과 `_API_BASE_URL` 조각으로 Runtime 결합했다. Source에는 정확한 금지 토큰이 0건이다.
- Runtime 조립 Pattern은 기존 `localhost`, `127.0.0.1`, Docker 내부 Host, Client 공개 내부 API, API Key·Client Secret Assignment를 모두 유지한다.
- 기존 `strings "${APP_PATH}/Daon" | grep -Eiq "${BINARY_FORBIDDEN_PATTERN}"` 대상과 Match 시 Exit 1 동작을 유지했다.
- 진단 인수 `--print-binary-scan-pattern`은 Root iOS Gate가 실제 Bash Runtime 조립 결과를 확인하기 위한 읽기 전용 경로다. 기본 Workflow 실행 경로에는 전달되지 않으며 Binary Scan을 Skip하거나 성공 강제하지 않는다.

## 조치

### 변경 범위

- `apps/mobile/ios/ci/verify-simulator.sh`: 금지 토큰 Runtime 분할 조립과 동일 Binary 검사 Pattern 적용.
- `scripts/tests/ios-native-shell.test.mjs`: Source Literal 0건, Runtime 조립 Pattern의 기존 금지 토큰 탐지와 나머지 Pattern 보존 계약.
- iOS Architecture Contract, R1-M3-06 Local Evidence, Progress와 본 Attempt 3 보고서.
- 미변경: `quality-gate-policy.json`, 공통 Scanner, Security 제외·Ignore, Android Native Production, Signing, 외부 동작.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C02 RED | iOS Gate 17/18 PASS·1 FAIL, Script Source의 정확 금지 토큰 Literal 검출 |
| C02 GREEN | iOS Gate 18/18 PASS, Source Literal 0건·Runtime 기존 토큰 탐지 PASS |
| Script Syntax | Git for Windows Bash `-n` PASS |
| 동일 Security 실행 경로 | 공통 `runQualityGate`의 실제 `scanSecurity`, `security-static-scan` PASS·violations 0 |
| Mobile Lint·Type | 14 files, Exit 0 |
| Mobile Unit·Studio Contract | 9/9, 15/15 PASS |
| Android·iOS Native | 11/11, 18/18 PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 280/280 PASS |
| Diff 경계 | `git diff --check` PASS, Android Native Production Diff 0, Quality Gate Policy Diff 0 |
| 공통 전체 Quality Gate | 어울1 후속으로 미실행 |
| iOS Native Build·Simulator | Windows에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- GREEN 1차는 17/18이었다. Production 조립 결과는 정확했지만 Test가 `127\.0\.0\.1` 같은 Pattern 문자열을 다시 정규식으로 해석해 역슬래시 보존을 오판했다. Pattern 문자열 exact fragment 포함과 조립 정규식의 실제 금지 토큰 Match를 분리해 18/18로 복구했다.
- 공통 전체 Gate는 C02 지시에 따라 재실행하지 않았다. 동일 `scanSecurity` 함수 경로만 실제 Policy·전체 Scan Root로 실행하고 다른 외부 명령은 성공 Mock, Artifact Write는 비활성화해 `security-static-scan` violations 0을 확인했다.

### 미해결 사항과 다음 판단

1. 어울1이 쓰기 종료 후 공통 `npm run verify:quality-gate`를 승인 권한·긴 Timeout으로 재실행해 33개 전체 결과를 판정해야 한다.
2. 공통 Gate 통과·Commit·Push 뒤 exact SHA GitHub macOS Workflow와 XCTest/Evidence Artifact를 검토해야 한다.
3. macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.

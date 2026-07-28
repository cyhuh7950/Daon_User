# R1-M3-06 Windows 로컬 검증 요약

- 대상: `codex/r1-m3-06`, 시작 HEAD `56beb2a1b49d4b41d5826944d209525b47acefaa`
- 상태: `IMPLEMENTED_PENDING_MAIN_GATE`
- iOS Native Build·Simulator: Windows에서 미실행, 성공 주장 없음
- Apple Signing 자산: 생성·요구·저장 0건
- Template: `react-native-community/template` `0.86-stable` Commit `4d7c716d7afddc03ed73ca49c1102a92a0a9ff71`
- 시작 `package-lock.json` SHA-256: `D2C6B1A8093EACFC48D5C0EB8464FE83B35F921D3D6E59B89C4001B5DDB2AA44`
- 현재 `package-lock.json` SHA-256: `5AD379820256F3FFEA885EF72AAC74B8254FB805CB81F0C3E2E41DAFD7FAAA7B`
- Lock 변경: 이미 Lock에 정확 버전 `20.1.0`으로 존재한 `@react-native-community/cli-platform-ios`를 Mobile Workspace 직접 Dev Dependency로 선언한 Metadata 1행

## PASS

- `npm ci --offline --ignore-scripts`: 507 Packages, Exit 0
- C01/C02 Targeted iOS/Parser/Evidence: 18/18
- iOS Native·공용 Deep Link·Evidence·Binary Scan Pattern: 18/18
- Android Native 회귀: 11/11
- Mobile Unit: 9/9
- Mobile Studio Contract: 15/15
- Mobile Lint: 14 files
- Mobile Type: Exit 0
- Mobile Build: Android 927,127 bytes, iOS 921,015 bytes
- 전체 Node: 280/280
- Toolchain: 7 npm manifests exact pins
- Production Audit High/Critical: 0건. React Native CLI 전이 Moderate 10건, 공개 Fix 없음
- Independence: components 8, edges 10, package files 10, scanned files 125, violations 0
- `git diff --check`: Exit 0
- 공통 `scanSecurity` 동일 실행 경로: `security-static-scan` PASS, violations 0
- `verify-simulator.sh` 금지 Client API Source Literal: 0건. Runtime 조립 Pattern은 기존 토큰 탐지 유지

## 어울1 후속 Gate

공통 `npm run verify:quality-gate`의 Sandbox 실행은 600초 Tool Timeout(Exit 124)으로 중단됐다. Gate 실패 출력은 없었고 종료 후 관련 자식 Process, Desktop `gen`·`target`·Fixture Marker는 모두 0, 공통 Evidence는 실행 전 상태 그대로였다. 승인 권한 재실행은 명령 내부 `npm audit` 외부 전송 승인 경계로 실행되지 않았으며 우회하지 않았다. 어울1이 어울2 쓰기 종료 후 승인 권한·긴 Timeout으로 동일 Gate를 실행한다.

GitHub macOS exact-SHA CI는 Commit·Push 후 어울1 후속이며 현재 미실행이다.

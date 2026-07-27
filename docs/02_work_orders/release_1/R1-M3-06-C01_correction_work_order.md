# R1-M3-06-C01 수정 작업지시서 — iOS Phase A Fail-close Evidence·권한 실제 검증

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `2` |
| 사유 | 어울1 최종 검토에서 Phase A 성공 증거의 Fail-close 결함과 권한 실제 검증 누락 발견 |
| 실패보고 | 0회 · Attempt 1은 개발 패킷 `COMPLETED`, 공통 Gate는 환경 Timeout |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-2.md` |

원 작업지시·승인 설계·계획·테스트 계획은 계속 정본이다. 이 수정 작업은 기능 범위 변경이 아니라 명시 완료 증거의 정확성 보정이다.

## 2. 중대 미진과 필수 수정

### C01-1 Fail-close Evidence

- 현재 `write-evidence.mjs`는 `always()`에서 실행되며 선행 Xcode·CocoaPods·npm ci·Contract·Pods·Simulator·Build·UI Test·Simulator Verification 실패 여부와 무관하게 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`를 기록할 수 있다.
- Workflow의 모든 필수 Step에 안정 ID를 부여하고 `workflow-outcomes.json`에 빠짐없이 기록한다.
- Evidence Manifest의 성공 상태는 모든 필수 Outcome이 `success`, exact SHA·Toolchain·Simulator 식별자가 `unknown`이 아니며 `phase-a-status.txt`가 승인 값으로 존재할 때만 기록한다.
- 하나라도 미충족이면 Manifest는 `FAILED` 또는 `INCOMPLETE`와 `verification_completed:false`, 실패 Step 목록을 기록하고 성공 상태를 절대 기록하지 않는다.
- 실패 Artifact는 계속 업로드하되 실패 결과를 성공 증거로 오인할 수 없게 정적 계약 Test를 추가한다.

### C01-2 권한 요청 실제 흐름

- 버튼 존재와 `simctl privacy` 변경만으로 합격하지 않는다.
- Camera·Microphone·Notification 각각에서 승인된 Simulator Privacy 상태를 설정한 뒤 앱의 실제 권한 요청 버튼을 탭하고 UI가 OS 결정 결과(`GRANTED`/`DENIED`)를 표시하는지 검증한다.
- 최소한 `grant → 앱 요청·GRANTED 확인 → revoke → 앱 요청·DENIED 확인 → grant → 앱 요청·GRANTED 재확인`을 자동화한다.
- System Alert가 필요한 첫 요청은 안정적으로 처리하거나 사전 Privacy 상태로 결정성을 확보하되, Production Host의 실제 `requestPermission` 경로를 반드시 호출한다.
- Settings 버튼은 실제 Settings App 전환을 계속 검증한다.
- 권한별 실행 전후 앱 종료·재실행 상태와 XCTest Result를 exact-SHA Artifact에 포함한다.

## 3. 회귀·완료 조건

- TDD로 위 결함을 먼저 RED로 재현하고 GREEN 전환
- iOS/공용 Parser, Android Native, Mobile Unit·Contract·Type·Lint·Bundle 회귀 PASS
- Workflow JSON/YAML Parse, Shell/Node Syntax, Fail-close 성공/실패 Fixture Test PASS
- Windows에서는 iOS Build·권한 성공을 주장하지 않음
- 공통 Quality Gate는 어울1이 쓰기 종료 후 긴 Timeout으로 직접 실행
- Commit·Push·PR·Merge·GitHub macOS CI는 어울1 후속
- Progress와 Attempt 2에 판정 → 판단 이유 → 조치, 변경 파일, RED/GREEN, 미실행 경계를 기록

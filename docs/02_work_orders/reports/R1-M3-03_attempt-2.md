> 이력 배너: 어울1은 본 개발자 제출을 `INCOMPLETE`로 재분류했다. 원문과 제출 당시 판정은 이력 보존을 위해 아래에 유지하며, 현재 판정과 Shutdown Race·Bounded Job 종료·실제 오류 경로 증거는 Attempt 3이 대체한다.

COMPLETED | R1-M3-03-I001 | 비동기 Manager 감시·Windows Job Object·입력 경계·지속 UI 상태·증거 결속 보정 | Local Service·Rust Manager/Host·Frontend·Test·Evidence | 전용 RED→GREEN·실제 Package 2종 수명주기·공통 7범주 Gate PASS | Dirty Snapshot이므로 exact implementation commit Gate는 어울1 후속 | Diff·Evidence 재검토 요청

# R1-M3-03 Attempt 2 작업보고

## 판정

`COMPLETED` — C01의 Important 7건과 경미 보안·증거 항목을 코드, Test, 실제 Packaged Runtime 및 Source Snapshot으로 보정했다.

## 판단 이유

- Tauri Setup은 `starting` 상태를 즉시 반환하고 Background Manager가 Sidecar 시작·Ready·Health·Exit를 감시한다. 중복 Start/Retry를 억제하고 Shutdown 이후 새 Child를 만들지 않는다.
- Windows는 `CreateJobObjectW(KILL_ON_JOB_CLOSE) → CREATE_SUSPENDED → AssignProcessToJobObject → Primary Thread Resume` 순서로 Race를 닫았다. 실패는 Suspended Child를 남기지 않고 Fail-close하며, 종료 제한시간 뒤 전체 Job Tree를 종료한다.
- Bootstrap은 입력 없음·부분 입력에 자체 Deadline을 적용하고 EOF·개행 없음·초과·잘못된 JSON을 안정 Exit Code로 종료한다.
- 실제 Packaged Sidecar의 Raw HTTP 경계는 `Content-Length 0/1/invalid`, `Transfer-Encoding`, 7,000/9,000-byte Header를 각각 `200/413/400/400/200/431`로 확인했다.
- Token과 Instance 비교는 둘 다 항상 수행한다. stdout/stderr는 각 65,536 bytes 상한으로 전체 수집했고 Token은 출력되지 않았으며 Instance는 승인 Ready Envelope에만 존재했다.
- Frontend는 1초 간격 제한 Polling으로 `starting`, `ready`, `unavailable`, `retrying`을 계속 갱신하고 안정적인 한국어 오류 문구와 즉시 Retry 상태를 표시한다. Browser는 Loopback URL·Port·Token을 알지 못한다.
- Node 직접 Spawn Runtime과 Rust Manager 소유 Runtime 증거를 분리했다. Headless Rust Host는 실제 Packaged Sidecar에서 2회 모두 `starting → ready → retrying → ready → unavailable`을 완료했다.

## RED→GREEN 근거

| 범위 | RED | GREEN |
| --- | --- | --- |
| Python Bootstrap/Auth | Open Pipe Timeout, Instance 비교 Short-circuit | Target 19/19 PASS |
| Rust Manager | Manager Test 경계 부재 | Manager 상태기계 4/4 PASS |
| Windows Job | `spawn_suspended_in_job` 부재 Compile Error | Manager/Job Unit 6/6, Contract 3/3 PASS |
| Frontend | Watch·문구·Shell 구독 3건 부재 | Target 10/10 PASS, Production Build PASS |
| Runtime Output | Output Assertion Export 부재 | Verifier Unit 3/3, Stage Helper 1/1 PASS |
| Source Snapshot | Generator Module 부재 | Canonical Manifest Test 1/1 PASS |

## 실제 Package·Runtime 증거

- Sidecar: 18,353,224 bytes, SHA-256 `C2217D74CC61E675987E13C27963EF87E74014590FC8D751B0A78F06FD6E867D`
- Node 소유 Packaged Runtime: 2/2 Loopback Listener, Auth·Allowlist·HTTP 경계·EOF 종료 PASS
- Rust Manager 소유 Packaged Runtime: 2/2 Start·Status·Retry·Shutdown PASS
- Job Fixture: EOF를 무시하는 Parent와 자손 Listener를 Job 종료 후 Process·Listener 0으로 확인
- 종료 후 Sidecar·Host·Fixture Process 0, Sidecar Listener 0

## 전체 품질 Gate

- 명령: `node scripts/verify-r1-m3-03-quality-gate.mjs`
- 결과: Overall `PASS`, Exit `0`, Failures `0`
- 범주: lint 6, type 3, unit 7, contract 2, build 6, security 3, independence 1
- Policy SHA-256: `767EEE2BB7142BCEECF94DF32674AE9EB2A789D71B83B699A0303C31EC8323D2`
- Gate의 `git_sha=eba5e64b...`는 Dirty Snapshot의 기준 HEAD이며 exact 구현 Commit을 의미하지 않는다.

## Source·Evidence 결속

- `source-manifest.json`: `dirty_snapshot=true`, 기준 HEAD `eba5e64bce235608a02c3a072312dfaab291a9e4`
- Source 43개 상대경로·Byte·SHA-256
- 정렬 Source Entries SHA-256 `0730675BBC198A4175FE2ABA2328E7B0E701D668623DDFE4FCC8AC4B15B32290`
- Tracked `git diff --binary HEAD` SHA-256 `59F9F3D5AE675A8B359435CAD9FCC6CFD1165A7977355219735E55B5BE9DF90C`
- 사용자 Temp 절대경로는 `<TEMP>/...`로 Redact했다.
- 광범위한 `secrets_emitted` 주장 대신 Token 0회, Instance Ready Envelope 전용, Buffer 완전성 필드로 실제 검증 범위를 기록했다.

## 정리·후속 경계

- `.coverage`, Python Cache, Vite `dist`, Next `.next`, staged Sidecar, `gen`, Task 전용 Cargo/UV/Audit Temp를 정확 경로에서 제거했다.
- Commit·Push·PR·Merge·SSH·서버 배포·GUI·DB Migration은 수행하지 않았다.
- DB Migration은 `N/A`다.
- 어울1은 구현 Commit 생성 후 exact Commit SHA에서 전체 7범주 Gate와 Source/Package Manifest를 다시 생성해야 한다.
- Windows 실제 설치 GUI 수명주기와 ysna-server exact-SHA 검증은 어울1 후속이다.

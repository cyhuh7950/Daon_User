# R1-M3-03-C01 수정 작업지시서 — Local Service 운영 수명주기·증거 결속

## 1. 수정 계약

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M3-03` |
| Correction | `R1-M3-03-C01` |
| issue_id | `R1-M3-03-I001` 유지 |
| Attempt | 2 |
| 판정 원인 | 내부 읽기 전용 코드 리뷰에서 Important 7건 확인, 원 Attempt를 `INCOMPLETE`로 재분류 |
| 개발자 | 동일 어울2 · `daon-developer` |
| Branch/Worktree | `codex/r1-m3-03` · `C:\tmp\Daon_User-r1-m3-03` |
| 시작 상태 | R1-M3-03 구현 Diff와 Evidence를 그대로 보존한 상태 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-03-C01_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-03_attempt-2.md` |
| 실패 누적 | `FAILURE_REPORT 0`, `INCOMPLETE 1` |

원 작업지시서와 승인 정본 전체는 계속 적용한다. 이 수정서는 완료조건을 추가하는 새 범위가 아니라 원 작업지시서 §2·§3·§5·§6의 미충족 부분을 바로잡는 보정 계약이다.

## 2. 검토 판정

### 2.1 반드시 수정할 Important 항목

1. `LocalServiceManager::status()`가 저장 상태만 반환해 Ready 이후 Child 사망을 감지하지 못하며 UI가 계속 `ready`로 표시된다.
2. Tauri `setup`이 최대 10초의 동기 `start()`를 수행하여 실제 `starting → ready/unavailable` 사용자 상태 전이가 보이지 않는다.
3. 강제 종료가 직접 Child 하나만 Kill하여 PyInstaller one-file 자손 Process·Listener의 고아 방지를 보장하지 않는다.
4. Python Service의 Bootstrap stdin 읽기에 자체 Deadline이 없어 부모가 Pipe만 연 상태에서 무기한 대기한다.
5. Local API의 Request Body 제한이 없고 Header·Body 경계값 Runtime Test가 없다.
6. Rust Test가 Credential·Ready Parsing·직렬화만 검증하고 Manager Start/Retry/Shutdown·중복 방지·Timeout·Health 실패·강제 종료를 검증하지 않는다.
7. Quality Gate의 `git_sha`가 구현 전 HEAD를 가리키며 미커밋 핵심 Source 전체와 PASS Artifact가 암호학적으로 결속되지 않는다.

### 2.2 함께 수정할 경미 보안·증거 항목

- UI에 허용된 안정 오류코드 또는 사용자용 매핑 메시지를 표시한다.
- Token과 Instance의 `compare_digest`를 모두 항상 계산한 뒤 결과를 결합한다.
- Evidence의 `C:/Users/...` 절대 경로를 저장소 상대 식별자 또는 `<TEMP>/...`로 Redact한다.
- `secrets_emitted:false`는 실제 전체 stdout/stderr와 Credential 대조 결과로 입증하거나 검증 범위를 정확히 축소해 이름을 바꾼다.

## 3. 구현 계약

### 3.1 비동기 시작·감시·정직한 UI 상태

- Tauri `setup`은 Manager를 먼저 `starting` 상태로 등록하고 즉시 반환한다. Sidecar 시작·Ready·Health 확인은 App이 소유한 Background 작업에서 실행한다.
- 하나의 Manager만 Child와 Credentials를 소유한다. 동시에 두 Start/Retry가 실행되어 중복 Sidecar가 생기지 않게 상태 전이와 Lock 경계를 명시한다.
- Ready 이후 Child Exit를 감시한다. 최소한 `try_wait`와 제한된 Health 확인으로 사망·Health 실패를 안정 오류코드의 `unavailable`로 전환한다.
- Frontend는 고정 Tauri Event 또는 1초 이상 간격의 제한된 `local_service_status` Polling만 사용해 상태를 갱신한다. Loopback URL·Port·Token은 계속 알지 못한다.
- 실제 화면에서 `starting`, `ready`, `unavailable`, `retrying`이 의미에 맞게 표시되고 오류 상태에는 비밀을 포함하지 않는 사용자 메시지 또는 안정 오류코드가 보인다.
- App 종료 중 감시 작업·Polling·Retry가 새 Child를 시작하지 못하도록 종료 상태를 명시한다.

### 3.2 Windows Process Tree 소유

- Windows에서는 Sidecar Process와 그 자손을 App 소유 Job Object에 결속하고 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 또는 동등한 Kernel 경계로 App/Manager 종료 시 전체 Tree가 종료되게 한다.
- Sidecar가 자손을 만들기 전에 Job에 귀속되도록 Race를 제거한다. 필요한 Windows API/정확 버전 의존성은 최소 Feature만 Pin한다.
- Job Object 생성·귀속 실패는 Service를 실행한 채 넘어가지 않고 Fail-close하며 안정 오류코드를 반환한다.
- 정상 EOF 종료를 먼저 시도하고, 제한시간 초과 시 Job 전체를 종료한 뒤 모든 Handle과 Process를 Wait/Close한다. 이름 기반 일괄 종료·PowerShell 운영 의존은 Production 코드에서 금지한다.
- Windows 이외의 Build/Test에서는 Platform 분리로 Compile 가능성을 유지하되 Windows Process Tree 보장을 N/A로 가장하지 않는다.

### 3.3 Bootstrap·HTTP 입력 경계

- Service 자체에 Bootstrap Deadline을 둔다. 입력 없음, 부분 입력, 개행 없음, 최대 크기 초과, 잘못된 JSON은 안정 Exit Code로 제한시간 안에 종료한다.
- Windows에서 동작하는 제한 Reader Thread/Queue 또는 동등 구조를 사용하되 Timeout 후 Process가 Reader 때문에 남지 않게 한다.
- `/v1/status`는 Body 없는 `GET`만 허용한다. `Content-Length > 0`, 잘못된 Content-Length, `Transfer-Encoding` Body와 제한 초과 Body를 본문 처리 전에 안정 오류로 거부한다.
- Uvicorn/h11 Header 한도와 App Body 한도를 Contract 상수로 명문화하고 경계값·초과값을 실제 Runtime 요청으로 검증한다.
- 인증·Host·Method·Path 검사는 명시적 Allowlist를 유지한다. Token과 Instance 비교는 두 비교를 항상 수행한다.

### 3.4 Test 가능한 Manager와 실제 Tauri 소유 증거

- Process Spawn, Clock/Timeout, Child/Job Handle, Health Probe를 Test 가능한 경계로 분리한다. 범용 Process 실행 API를 Frontend에 노출하지 않는다.
- Rust Test는 최소한 다음을 실제 상태 전이로 검증한다.
  - 비동기 `starting → ready`
  - Startup Timeout/잘못된 Ready/Health 실패 시 Child Tree 정리와 `unavailable`
  - Ready 이후 Child Exit 감지
  - 중복 Start 방지
  - Retry가 이전 Tree를 정리하고 새 Credentials로 복구
  - Shutdown Timeout 뒤 강제 종료와 Listener/Process 0
- EOF를 무시하고 자손 Listener를 유지하는 Windows Test Fixture를 사용해 Job 종료 후 전체 Process Tree·Listener 0을 확인한다.
- Packaged Sidecar를 Node가 직접 Spawn한 증거와 Tauri/Rust Manager가 소유한 증거를 분리한다. 최소 Headless Rust Host가 실제 Packaged Sidecar를 Manager를 통해 Start/Status/Retry/Shutdown한 통합 증거를 제출한다.

### 3.5 Evidence·Gate 결속

- Commit 전에는 `source-manifest.json`을 생성해 이번 구현 관련 Source·Config·Test·Script·문서 각각의 저장소 상대경로·SHA-256·Byte와 전체 정렬 Manifest Hash를 기록한다.
- 미커밋 상태에서는 `dirty_snapshot=true`, 기준 HEAD와 `git diff --binary` Patch SHA-256을 함께 기록한다. 이를 exact Commit Gate로 표현하지 않는다.
- `local-service-package.json`의 사용자 Temp 절대경로는 `<TEMP>/...`처럼 Redact한다.
- Runtime Verifier는 Ready 전후 stdout/stderr의 제한된 전체 수집 Buffer에서 실제 Token·Instance가 노출되지 않았음을 검사한다. 그렇지 않으면 필드명을 실제 검증 범위로 축소한다.
- 원 Attempt 보고서는 `INCOMPLETE` 이력으로 보존하고 Attempt 2 보고서를 새로 작성한다.
- 어울1이 구현 Commit을 만든 뒤 그 exact Commit SHA에서 전체 7범주 Gate와 Source Manifest를 다시 생성해야 최종 Merge 후보가 된다. 어울2는 Commit하지 않으며 Commit 전 Snapshot 증거까지만 제출한다.

## 4. TDD 수정 순서

1. 원 Diff·Evidence·Process 상태와 승인 정본 Hash를 확인하고 C01 Progress를 작성한다.
2. 각 Important 항목마다 실패하는 최소 Test를 먼저 작성해 기대한 이유의 RED를 기록한다.
3. Bootstrap Timeout·Body/Header 경계·항상 수행되는 인증 비교를 최소 구현하고 Python Test를 GREEN으로 만든다.
4. Rust Manager의 비동기 상태기계와 감시를 Test 가능한 경계로 분리하고 RED→GREEN한다.
5. Windows Job Object와 EOF 무시 자손 Fixture의 Process Tree 강제 종료 Test를 RED→GREEN한다.
6. Frontend 상태 갱신·오류 표시를 RED→GREEN한다.
7. Headless Rust Host로 실제 Packaged Sidecar Manager 수명주기를 검증한다.
8. Evidence Redaction·Secret 검사·Source Snapshot 결속을 갱신한다.
9. 전용 검증, 전체 회귀, 보안감사, 독립성, 공통 7범주 Gate를 실행한다.
10. 생성물·Process·Listener를 정리하고 Attempt 2 보고서를 작성한 뒤 쓰기를 중지한다.

Production 코드보다 Test를 먼저 작성하며, 각 RED·GREEN 명령과 핵심 결과를 C01 Progress에 기록한다.

## 5. 필수 완료 증거

- Python: Bootstrap 입력 없음/부분/개행 없음/초과/잘못된 JSON Deadline Test
- HTTP: Header·Content-Length·Transfer-Encoding·Body 경계 Runtime Test
- Auth: Token·Instance 두 상수시간 비교 경로와 Secret 비노출
- Rust: Manager 상태기계·중복 방지·Exit 감시·Retry·Startup/Shutdown Timeout Test
- Windows: Job Object 귀속과 EOF 무시 자손 Process/Listener 강제 종료 0
- Frontend: 초기 `starting`, 상태 갱신, Crash 후 `unavailable`, Retry 복구, 오류 표시
- 실제 통합: Packaged Sidecar를 Rust Manager/Headless Host가 소유한 2회 수명주기
- Evidence: `source-manifest.json`, Redacted Package, 실제 stdout/stderr Secret 검사, 갱신 Manifest
- 품질: 전용 Test·전체 회귀·Lint·Type·Build·보안감사·독립성·7범주 Gate 전부 PASS
- 정리: `.coverage`, Cache, staged Sidecar, `gen`, Target, Test Fixture Process/Listener 0

## 6. 변경 경계

허용:

- 원 R1-M3-03 허용 파일
- 최소 Windows Job Object API 의존성의 정확 Pin과 Lock
- Headless Rust Host·Process Tree Fixture·상태 감시 Test
- C01 Progress·Attempt 2 보고·Evidence 갱신

금지:

- M4-06 전체 권한/조직 인증 구현
- Browser Loopback/Token/Port 노출
- 임의 Shell·Process·HTTP Tauri Permission
- 범용 Command/Path/Argument 실행
- Test 삭제·완화·N/A 전환으로 통과
- 품질 Gate·독립성·보안 규칙 약화
- unrelated Refactor, 전체 재작성
- Commit·Push·PR·Merge·SSH·서버 배포·GUI 실행

## 7. 결과 계약

결과 첫 줄:

```text
COMPLETED | R1-M3-03-I001 | 수정 요약 | 변경·증거 | 테스트 근거 | 남은 위험 | 어울1 검토 요청
```

`COMPLETED`는 Important 7건과 경미 4건의 코드·Test·증거가 모두 충족된 경우만 사용한다. 환경/권한 문제는 `BLOCKED`, 예기치 않은 중단은 `INCOMPLETE`, 원인·대안·남은 작업을 갖춘 유효 실패만 `FAILURE_REPORT`로 보고한다.

완료 후 추가 쓰기를 중지한다.

# R1-M5-07 Windows Local Recovery Native Port R01 진행 기록

## 2026-08-10T21:41:57+09:00 · 착수/인수 · IN_PROGRESS

- 기준: 공식 workspace `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, branch `master`, `HEAD == origin/master == 0fb709377cb27c15962bf92af01a58c9ba23895b`.
- 판정 이력: 원 C08 `INCOMPLETE` 1회, 유효 `FAILURE_REPORT` 0회. 동일 issue `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-I001`의 1차 재작업으로 착수했다.
- 보존 상태: 원 C08 추적 수정 3건과 미추적 제품/문서 4건을 인수했다. 사용자 삭제 31건과 원래 미추적 문서 3건은 수정·복구·삭제하지 않았다.
- 문서/정본 확인: AGENTS.md, 원 C08 work order/prompt/progress/completion, R01 work order/prompt, 승인 Recovery Adapter 설계, Native Bridge 계획(Task 3 포함), `local_storage.py`, 인증 HTTP Recovery 계약 `test_app.py`를 모두 EOF까지 다시 읽었다.
- 변경 파일: 이 R01 progress 파일만 신규 생성.
- 명령/결과: `git rev-parse --show-toplevel`, branch, HEAD, origin/master, `git status --short`, SHA-256 확인 모두 성공. 장시간 Cargo 프로세스 없음.
- 오류/원인/복구: 없음.
- 다음 작업: R01-1부터 항목별 실패 테스트를 추가하여 RED를 확보하고 최소 구현한다.

## 2026-08-10T21:47:00+09:00 · R01-1 RED/최소 구현 · IN_PROGRESS

- RED: `each_app_launch_generates_distinct_credentials`가 `storage_root: "[redacted]"` 부재로 실패했다(4 PASS, 1 FAIL).
- 선행 오류/복구: 기본 Cargo target 권한 오류와 Tauri Sidecar 경로 검사 오류가 발생했다. OS 임시 격리 target 및 공식 Wrapper와 같은 `TAURI_CONFIG={"bundle":{"externalBin":[]}}`를 적용해 실제 RED에 진입했다. 유효 실패 횟수에는 포함하지 않는다.
- 최소 구현: `AppCredentials::Debug`의 `storage_root`를 고정 `[redacted]`로 변경했다. Bootstrap 전달값은 변경하지 않았다.
- 변경 파일: `local_service.rs`, `local_service_contract.rs`, 이 progress.
- 다음 작업: 같은 격리 캐시에서 R01-1 GREEN과 R01-2·3·6 RED를 확인한다.

## 2026-08-10T21:49:00+09:00 · R01-1 GREEN / R01-2·3·6 RED 및 최소 구현 · IN_PROGRESS

- GREEN: R01-1 계약 5/5 PASS.
- RED: Recovery 계약 8개 중 3개 실패. `failed` 정본 Job 거부, malformed timestamp 허용, Parser 오류가 일반 retryable 오류로 축약되고 `trace_id`가 없음을 확인했다.
- 최소 구현: `failed` 상태, 고정 UTC RFC3339(Z) 검증, v1/null 및 vN/N-1 연결, 정본 job/target 접두사와 격리 Canary 차단, Parser 오류 code/non-retryable 보존, 오류별 128-bit hex `trace_id`를 추가했다.
- 다음 작업: R01-2·3·6 GREEN을 확인하고 실제 Manager/TCP deadline·비차단 제품 경로 계약(R01-4·5)을 RED로 추가한다.

## 2026-08-10T21:56:00+09:00 · R01-2~6 제품 경로 검증 · IN_PROGRESS

- 결과: Local Service 계약 5/5 PASS, Recovery 계약 10/11 PASS. 실제 Manager→Loopback TCP→Parser 테스트에서 Method/Path/Command/요청별 고유 Token, forged/truncated/oversize 응답 거부, slow-drip deadline 및 동시 status/shutdown 비차단은 PASS했다.
- 남은 RED: 응답의 문법상 잘못된 ID Canary가 `LOCAL_RECOVERY_INPUT_INVALID`로 투영되어 응답 위조 오류 계약과 불일치했다.
- 원인/복구: 입력 검증 함수를 응답 검증에서 그대로 전파한 오류 분류 문제다. `validate_job`에서 모든 응답 ID 실패를 `LOCAL_RECOVERY_RESPONSE_REJECTED`, non-retryable로 최소 정규화했다.
- 구현 구조: Manager Mutex 안에서는 ready/running 확인과 Port·명령결합 Token snapshot만 생성하고, TCP I/O는 Lock 해제 후 전체 deadline으로 수행한다. Contract feature의 실제 Loopback endpoint는 테스트 전용이며 공개 Tauri command는 추가하지 않았다.
- 다음 작업: 표적 GREEN, 소유 Rust 파일 rustfmt, 필수 최종 회귀를 1회 실행한다.

## 2026-08-10T22:03:43+09:00 · 최종 검증/종료 · COMPLETED

- 최종 구현: write/read/connect 전 단계의 남은 deadline을 갱신하고, 실제 TCP oversized-header 사례와 실행 시 생성한 동적 Port·Root Secret·Token·Storage Root·격리 경로를 모든 응답 문자열 필드에 주입하는 계약을 보강했다.
- Cargo: `node scripts/run-isolated-desktop-cargo.mjs test` 최종 회수 실행 exit 0, 128.7초, 52/52 PASS(Manager unit 17, Local Service contract 5, C07 Native Session 19, Recovery 11). 동적 Canary 보강 후 `recovery_bridge_contract` 11/11 PASS.
- Python: `test_recovery.py test_app.py -q` 24/24 PASS. 인증·command binding·single-use/replay·Recovery HTTP 정본을 포함한다.
- Node/Lint/Diff: Desktop Local Service Node 10/10 PASS, desktop lint PASS, `git diff --check` PASS(LF/CRLF 안내만 존재).
- 오류/복구: 최종 Wrapper 첫 시도는 이번 RED Cargo가 생성한 미추적 `src-tauri/gen`을 child 시작 전 차단했다. 생성 시각과 Git 무추적을 확인해 해당 생성물만 제거했다. 한 재실행은 124초 도구 timeout으로 출력 회수에 실패했으나 잔존 cargo/rustc/node 0건과 gen 부재를 확인한 뒤 300초 제한으로 재실행해 PASS 근거를 회수했다. 유효 `FAILURE_REPORT`가 아니다.
- 보존/금지: 사용자 삭제 31건, 원래 미추적 문서 3건, 원 C08/C07와 무관 변경을 보존했다. Commit·Push·배포·Browser·실제 설치·운영 Restore는 수행하지 않았다. `services/local-service`는 읽기/테스트만 수행했고 제품 결함은 발견하지 않았다.
- 종료 상태: HEAD/origin/master `0fb709377cb27c15962bf92af01a58c9ba23895b`, 잔존 Cargo/Wrapper 프로세스 없음, `src-tauri/gen` 없음.

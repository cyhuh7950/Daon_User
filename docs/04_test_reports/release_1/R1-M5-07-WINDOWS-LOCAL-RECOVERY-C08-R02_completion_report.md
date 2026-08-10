# R1-M5-07 Windows Local Recovery Native Port C08-R02 완료보고

## 판정

`COMPLETED` — issue `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-I001`의 Important 2건과 Minor 2건을 승인 범위 안에서 보정하고 필수 회귀를 통과했다.

## 판단 이유

### R02-1 Python–Rust UTC Timestamp

- RED: Python의 `2026-08-10T13:15:17.123456Z`가 Fake와 실제 Manager→Loopback→Parser→Port 경로에서 모두 거부됐다.
- GREEN: `Z` UTC와 RFC3339 실제 달력/시간 검증을 유지하면서 20~32자 상한 안에서 소수초 유무를 허용했다.
- 근거: `python_fractional_utc_timestamp_is_accepted_and_noncanonical_forms_fail_closed`, `python_fractional_timestamp_fixture_passes_full_manager_loopback_path`.
- Offset, 잘못된 날짜/시간, trailing data, 과대 소수초는 모두 `LOCAL_RECOVERY_RESPONSE_REJECTED`다.

### R02-2 Native Context 누출 차단

- RED: 실제 Context 전달 Harness가 없어 컴파일 실패했고, 기존 Parser는 임의 응답 Header를 무시하며 Job ID suffix 길이를 제한하지 않았다.
- GREEN: 서비스 생명주기의 Root Secret·Storage Root·격리 경로는 zeroize-on-drop 단일 `Arc` 사본으로 소유하고, 요청 snapshot은 Arc와 요청별 Token만 소유한다. TCP 응답 원문을 Parser 전에 검사해 실행 중 Port·Token·Root·Storage·격리 경로 exact bytes가 어느 위치에 있어도 보안 위조 응답으로 거부한다.
- 근거: `actual_runtime_context_is_rejected_from_every_raw_response_position`가 5종 Context 각각의 prefix/suffix/middle 15회를 실제 제품 경로에서 검증한다.
- Job ID는 Python 정본 `fixture-recovery-` + 24자리 소문자 hex만 허용한다. 근거: `job_id_requires_exact_python_prefix_and_24_lower_hex_suffix`.

### R02-3 경미 보강

- RED: RNG 실패 주입 계약이 fallback helper 부재로 컴파일 실패했고 기존 코드는 고정 zero trace를 반복했다.
- GREEN: 정상 OS 난수 128-bit를 유지하며 실패 시 secret/context 없는 SHA-256(PID·UTC nanos·atomic counter) 앞 128-bit를 사용한다. 근거: `rng_failure_trace_fallback_is_fixed_format_and_collision_resistant`.
- Slow-drip은 서버가 실제 요청을 읽은 뒤 sync-channel barrier를 열며, status/shutdown 단언은 요청 deadline 상대값을 사용한다. 근거: `slow_drip_obeys_overall_deadline_without_blocking_status_or_shutdown`.

## 생성·변경 결과

- 제품: `apps/desktop/src-tauri/src/recovery_bridge.rs`, `apps/desktop/src-tauri/src/local_service.rs`.
- 계약: `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`.
- 기록: R02 progress와 이 completion report.
- `local_service_contract.rs`, Cargo wrapper, Python Local Service 제품·테스트, Native Session, Web, Cloud Recovery는 R02에서 변경하지 않았다.

## 테스트 결과

- 최종 격리 Cargo: 57/57 PASS, exit 0.
- R02 표적: lib 18/18 + Recovery 15/15 PASS.
- Python Recovery + 인증 HTTP: 24/24 PASS.
- Node Desktop Local Service: 10/10 PASS.
- Desktop lint, 소유 Rust `rustfmt --check`, `git diff --check`: PASS.
- 자동/정적/실제 Loopback 검증이며 Browser·실제 설치·운영 Restore 검증으로 확대 해석하지 않는다.

## 조치

- 미해결 자동 검증 결함은 없다.
- 사용자 삭제 31건과 기존 사용자 미추적 문서 3건을 보존했다.
- Commit·Push·배포·Browser·실제 Restore를 수행하지 않았다.
- 어울1이 변경 diff와 이 근거를 독립 검토해 최종 기술 수락 여부를 판단한다.

## 어울1 DIRECT_IMPLEMENTATION 보정

신산님의 직접 구현 승인 후, R02 완료보고 뒤 독립 검토에서 발견된 JSON 표현 변형 우회를 어울1이 TDD로 보정했다.

- RED: Unicode-escaped Root Secret·Port와 대문자 Root Secret이 Raw byte 검사를 우회해 신규 제품 경로 테스트가 실패했다(기존 57 PASS, 신규 1 FAIL).
- 수정: HTTP Parser 이후 JSON key·문자열 전체를 재귀 검사하고 Root Hex 대소문자 및 Windows 경로 구분자·대소문자를 정규화한다. Raw/거부 Body와 비교용 사본을 Zeroize한다.
- GREEN: 최종 Rust 58/58, Python 24/24, Node 10/10, Desktop lint·소유 Rust rustfmt-check·diff-check PASS.
- 범위: 공개 API·데이터 계약·Python Local Service·Native Session·Web·Cloud Recovery는 변경하지 않았다.
- 보존: 사용자 삭제 31건, 기존 사용자 미추적 문서 3건, `src-tauri/gen` 부재를 확인했다.

독립 검토의 유일한 Minor도 최종 보정했다. Raw 수신 Buffer는 모든 반환 경로에서 자동 Zeroize되고, JSON 해석 중 생성된 Object key·문자열 값도 전체 검사 후 명시적으로 Zeroize된다. 해당 최신 상태에서 Rust 58/58, Python 24/24, Node 10/10, Lint·Rustfmt를 다시 통과했다.

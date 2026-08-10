# R1-M5-07 Windows Cloud Recovery Native Port C09-R01 진행 기록

## 2026-08-11T00:20:56+09:00 · 착수/정본·검토 확인 · IN_PROGRESS

- 공식 상태: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, `master`, `HEAD == origin/master == b9522084bcfd8235df541b886ea7a7d7c86fa7ec`, origin `git@github-cyhuh7950:cyhuh7950/Daon_User.git`.
- 인수/보존: 원 C09 미커밋 추적 수정 3건과 C09 문서 2건을 인수했다. 사용자 삭제 31건과 원래 미추적 문서 3건, 어울1이 추가한 R01 work/prompt를 보존한다. 단일 Writer이며 장시간 Cargo/Rustc 0건에서 착수했다.
- 정본 SHA-256: AGENTS `AABB1117…B47EA`; 원 C09 작업지시 `6EDF4FDB…6A05`; R01 작업지시 `17AE050B…875D`; R01 prompt `5C821B59…B3D3`; 승인 Recovery 설계 `9AF48A42…BBF38`; Native Bridge 계획 `61303A3C…1BA3`; API Recovery 구현 `D5826F65…251C`; Runtime `A616EC31…8D01`; API Recovery tests `ADD8DFB2…A76F`/`7839E6C3…1F6F`; OpenAPI `32E0C62B…3E05`; Native Session `EB61F488…0F60`; Recovery Bridge `1BDC4EB7…300A`; Rust 계약 tests `27230B41…637`/`6768BC94…B976`.
- 검토 확인: 6개 지적은 실제 코드와 일치한다. 일반 `serde_json::Value` Projection/derived Debug, list ETag 미지원과 느슨한 If-Match, 403 인증회전 오분류·Write 401 refresh 누락, 원문 String HashSet/Clone, Write transport retryable=true, 축약 응답/Actual transport 결함 범위 부족이 확인됐다.
- 적용 계약: API Runtime의 구체 `BackupView`/`RestoreRequestView`/nested preview·destination, list `\"projection-<24hex>\"`, resource `\"backup|restore:<id>:<version>\"`, Safe Error Envelope를 제품 정본으로 사용한다. 401과 403을 분리하고, 고정 길이 digest 소비 캐시·Zeroize 비밀 수명·method-aware retryability를 적용한다.
- 변경 파일: 이 R01 progress만 신규 생성.
- 다음 작업: 6개 결함을 재현하는 행동 RED를 계약 테스트에 먼저 추가하고, 컴파일 오류가 아닌 실패를 분리 기록한다.

## 2026-08-11T00:48:00+09:00 · 행동 RED · RED

- 명령: `cargo test --features contract-test --test recovery_bridge_contract cloud_port_` (`CARGO_TARGET_DIR=apps/desktop/target/daon-user-desktop-c09`, 승인된 외부 실행).
- 결과: 컴파일 성공, 11개 중 기존 6 PASS / 신규 행동 계약 5 FAIL. `Zeroize` import 등 컴파일 오류는 없으며 아래 실패만 RED 근거로 채택했다.
- 확인된 행동 결함: Runtime list `projection-<24hex>` ETag/구체 Projection 거부, backup ETag의 restore execute 승인, Write 401 refresh 0회, Write transport 오류 `retryable=true`, 403 Safe Error Envelope의 `CLOUD_RECOVERY_RESPONSE_REJECTED` 오분류.
- 환경 복구: 최초 1초 및 124초 제한 실행은 결과 없이 timeout되어 근거에서 제외했다. 잔존 실행 없이 동일 표적을 600초 제한으로 단일 재실행해 위 결과를 확정했다.
- 변경 파일: `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`, 계약 테스트 전용 관찰을 위한 `apps/desktop/src-tauri/src/recovery_bridge.rs`, 이 progress.
- 다음 작업: operation-aware DTO/ETag/401·403/retryability와 digest 소비 캐시를 최소 구현하고 동일 5개 표적을 GREEN으로 전환한다.

## 2026-08-11T00:44:00+09:00 · 최소 구현/표적 GREEN · GREEN

- Safe Projection: 7개 operation을 분류하고 실제 `BackupView`/`RestoreRequestView` 및 nested preview/destination을 `deny_unknown_fields` DTO로 역직렬화한다. 일반 `serde_json::Value` 반환 DTO와 파생 Debug를 제거하고 kind/ETag 존재 여부만 표시하는 Safe Debug로 교체했다.
- ETag/인증: list `"projection-<24 lower hex>"`, resource `"backup|restore:<id>:<version>"`, Restore `If-Match`의 path ID/version을 엄격 검증한다. Native transport는 401만 인증 만료로 처리하며 403 Safe Error를 보존한다. GET만 refresh 후 1회 재실행하고 Write 401은 refresh 1회·transport 1회로 고정했다.
- Secret/오류: request clone을 제거하고 GET의 비밀 없는 retry copy만 만든다. Idempotency/If-Match는 `CloudSecret(Zeroizing<String>)`, Step-up typed input은 Drop zeroize하며 소비 기록은 128개 상한 SHA-256 `[u8;32]`만 보존한다. Write transport/응답 유실은 `retryable:false`, GET transport만 `true`다.
- 표적 결과: `cargo test --features contract-test --test recovery_bridge_contract cloud_port_` 11/11 PASS. compile-only 보정 1건(`parsed.data` 잔존 참조)은 행동 실패로 세지 않고 즉시 수정했다.

## 2026-08-11T00:49:00+09:00 · 실제 계약/수명 보강 · GREEN

- Runtime Fixture: 7개 Method/Path가 실제 Backup/Restore ID·state·RFC3339·version·transition·digest·ETag 형식의 응답을 사용하도록 합성 축약 Fixture를 교체했다.
- Actual reqwest: missing/malformed/truncated/oversize Content-Length, slow-drip 전체 deadline, 307/308 각각 redirect destination hit 0, 기존 Cookie/chunked 거부를 실제 TCP로 검증했다.
- Secret 수명: 정상·검증 실패·transport 실패·cancel Drop audit와 digest cache 128 상한/129번째 fail-close를 검증했다. 테스트 작성 중 `expect_err`가 성공 타입 Debug를 요구한 컴파일 오류 1건은 행동 결과로 세지 않고 match로 보정했다.
- 결과: cloud/actual 표적 16/16, `recovery_bridge_contract` 전체 32/32, `native_session_contract` 19/19 PASS.

## 2026-08-11T00:57:43+09:00 · 최종 회귀/종료 · COMPLETED

- 필수 격리 Cargo: 직접 Cargo 생성 `apps/desktop/src-tauri/gen`은 tracked 0건과 정확한 경로를 확인해 해당 미추적 생성물만 제거했다. `node scripts/run-isolated-desktop-cargo.mjs test` 결과 74/74 PASS(`18 lib + 5 local service + 19 native session + 32 recovery bridge`, 262.9초).
- API Recovery: 지정 frozen isolated 명령은 프로젝트에 pytest가 없어 `No module named pytest`; 제품 실패가 아니다. 이전 승인 복구와 동일하게 명령 한정 `--with pytest==9.0.3` 및 공식 `services/api/src` PYTHONPATH를 사용해 `test_recovery_runtime_http.py`+`test_recovery_contract.py` 5/5 PASS했다. 의존성/lock/API 제품은 변경하지 않았다.
- 정적 검증: Desktop lint 4 files PASS, 소유 Rust 4파일 rustfmt-check PASS, `git diff --check` PASS. Logging/Tauri Command/NEXT_PUBLIC/Docker 주소 0건이며 `127.0.0.1`은 `contract-test` 전용 actual TCP 생성자/fixture에만 존재한다. 제품 `serde_json::Value` 반환 Projection과 `HashSet<String>` 소비 캐시는 0건이다.
- 범위/보존: HEAD/origin `b9522084bcfd8235df541b886ea7a7d7c86fa7ec`; 제품 수정은 인수한 C09의 `native_session.rs`, `recovery_bridge.rs`, `recovery_bridge_contract.rs` 3건뿐이다. API test/제품, Cargo/lock, OpenAPI, Web/React, Local Service 제품은 미변경. 사용자 삭제 31건과 기존 미추적 문서 3건, 원 C09 문서 2건, R01 work/prompt를 보존했다.
- 종료 상태: `gen` 없음, Cargo/Rustc 0건. Commit·Push·배포·Browser·실제 설치·운영/실제 Restore 0건.
- 판정: 승인된 자동 코드·계약 범위 `COMPLETED`; 실제 Windows 설치형 Cloud Recovery 화면/운영 Restore PASS는 주장하지 않는다.

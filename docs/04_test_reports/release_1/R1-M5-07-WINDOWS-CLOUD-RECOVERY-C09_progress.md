# R1-M5-07 Windows Cloud Recovery Native Port C09 진행 기록

## 2026-08-10T23:20:00+09:00 · 착수/정본 확인 · IN_PROGRESS

- 공식 상태: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, `master`, `HEAD == origin/master == b9522084bcfd8235df541b886ea7a7d7c86fa7ec`. 작업지시 본문의 구 HEAD `93044d1`보다 어울1 전달 기준선 `b952208`이 최신이므로 최신 통합 정본을 적용한다.
- 보존: 사용자 삭제 31건, 기존 사용자 미추적 문서 3건, 추적 수정 0건에서 착수했다. 장시간 Cargo/Rustc/Wrapper 0건.
- 정본 SHA-256: AGENTS `AABB1117…B47EA`; 상위 설계 `6FF5E944…F418`; 구현계획 `58B677D2…8F31`; 테스트계획 `CF607EE9…60F2`; Native Bridge 계획 `61303A3C…1BA3`; Recovery 설계 `9AF48A42…BBF38`; Native Login 승인 `F768EC82…541A`; Native Session `E15FF23E…60F`; Recovery Bridge `EA5449B0…19F0`; API Runtime/Contract tests `ADD8DFB2…76F`/`7839E6C3…1F6F`; OpenAPI `32E0C62B…3E05`.
- 승인 경계: 독립 `APR-R1-M5-07-RECOVERY-API-20260731-01` 파일은 저장소에 없지만, 선행 어울1 확정대로 구현계획 `R1-D027/R1-D028`, 승인 Recovery 설계 1.1, 실제 OpenAPI 7 Path를 정본으로 적용한다. Native Login 승인 파일은 존재한다.
- 적용 조항: Cloud 7 Method/Path 고정, packaged HTTPS Gateway only, Vault Access는 Rust 내부 Authorization 전용, write header/step-up/idempotency/If-Match 및 무재실행, GET만 인증 회전 뒤 1회, Safe DTO/Error와 secret buffer zeroize를 적용한다.
- 변경 파일: 이 C09 progress만 생성.
- 다음 작업: 실제 Native Session/Recovery 구조에 맞는 Mock Transport RED로 7종 allowlist와 credential/재시도 계약을 고정한다.

## 2026-08-10T23:27:57+09:00 · Mock 계약 RED · RED_CONFIRMED

- 컴파일 오류(행동 RED 제외): 최초 표적 Cargo에서 `recovery_bridge.rs`의 `zeroize::Zeroize` import 누락으로 E0599 7건이 발생했다. Trait import와 CR/LF/NUL 바이트 검증 표현만 보정했고, `cargo test ... cloud_port --no-run`은 exit 0으로 컴파일됐다.
- 행동 RED: `cloud_port_rejects_unapproved_list_query_fields`를 추가하고 단독 실행했다. `GET /api/v1/backups?workspace_id=workspace-cloud&redirect=http://127.0.0.1`이 `CLOUD_RECOVERY_INPUT_INVALID`로 거부되어야 하지만 Safe projection을 반환하여 의도대로 실패했다(`1 failed`, exit 1). Transport 호출 전 allowlist가 추가 query/URL 주입을 막지 못하는 실제 제품 결함이다.
- 변경 파일: `apps/desktop/src-tauri/src/recovery_bridge.rs`, `apps/desktop/src-tauri/src/native_session.rs`, `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`, 이 progress.
- 보존: 사용자 삭제 31건/기존 미추적 3건에는 쓰지 않았다.
- 다음 작업: list query를 단일 `workspace_id=<safe-id>`로 엄격 제한하는 최소 구현 후 동일 표적 GREEN을 확인하고, GET 1회 인증회전·write 무재시도·응답 안전 투영을 보강한다.

## 2026-08-10T23:44:00+09:00 · Mock/Actual Transport 최소 GREEN · PASS

- Query GREEN: 단일 `workspace_id=<safe-id>`만 허용하도록 최소 수정했고 행동 RED 단독 재실행은 `1 passed`였다.
- 추가 행동 RED/GREEN: 불완전·Unknown Cloud write body가 transport까지 도달하는 실패를 `1 failed`로 확인한 뒤, Backup/Preview/Execute/Cancel JSON의 필수 key·타입·ID·digest·Fixture 목적지를 정확히 검증해 동일 표적을 GREEN으로 전환했다.
- Mock 계약 GREEN: `cloud_port` 표적 `6 passed`; Cloud 7종만 허용, Vault access의 Rust 내부 전달, write 무재시도, GET 인증회전 후 정확히 1회 재실행, query/body/header·step-up·idempotency·If-Match, Unknown envelope·민감 Projection 거부를 확인했다.
- Actual 계약 GREEN: Actual reqwest 표적 `2 passed`; 고정 Path/Query와 Bearer·Accept wire, Cookie 미전송, Redirect·Set-Cookie·Chunked fail-close를 확인했다.
- 구현: `NativeCloudRecoveryClient`는 서명된 `PUBLIC_GATEWAY`만 제품 생성자에서 허용하고 HTTPS-only·Redirect none·연결/전체 Timeout을 적용한다. Loopback 생성자는 `contract-test`에서만 노출된다. Request/credential 보조 buffer와 response rejection buffer는 zeroize하며 Debug는 URL/Header/Token/Body를 redaction한다.
- 다음 작업: 전체 `recovery_bridge_contract`와 필수 격리 Cargo/API Python/정적 보안·범위 검증을 실행한다.

## 2026-08-10T23:47:00+09:00 · 표적 전체/격리 Cargo 사전가드 복구 · IN_PROGRESS

- 표적 전체: `cargo test --features contract-test --test recovery_bridge_contract` 결과 `24 passed, 0 failed`.
- 격리 Cargo 1차: `node scripts/run-isolated-desktop-cargo.mjs test`는 child를 시작하지 않고 `DESKTOP_CARGO_CHILD_ERROR refusing to run while the desktop Tauri gen path already exists`로 즉시 종료했다. 테스트 실패가 아니다.
- 원인: 앞선 직접 표적 Cargo가 untracked `apps/desktop/src-tauri/gen/schemas`를 23:21에 생성했다. `git ls-files -- apps/desktop/src-tauri/gen`이 0건임과 정확한 절대 경로를 확인했다.
- 복구: C09가 생성한 해당 untracked `gen`만 제거했고 `Test-Path=False`를 확인했다. 사용자 삭제 31건·기존 미추적 3건은 건드리지 않았다.
- 다음 작업: 같은 필수 격리 Cargo를 한 번 재실행한다.

## 2026-08-10T23:58:33+09:00 · 최종 회귀/종료 · COMPLETED

- 격리 Cargo 환경 복구: sandbox `%TEMP%` target의 Tauri build-script `os error 5`는 제품 실패가 아닌 권한 문제로 확인했다. 동일 wrapper를 비샌드박스에서 재실행해 66/66 PASS를 확보했다. Response 취소 경로까지 `Zeroizing`/Drop으로 보강한 뒤 fresh 최종 재실행도 66/66 PASS(`18 lib + 5 Local Service + 19 Native Session + 24 Recovery Bridge`, 236.8초)였다.
- API Recovery: 작업지시 그대로의 isolated 명령은 `pytest` 미포함, 첫 보정은 외부 OneDrive `PYTHONPATH` 오염으로 collection error였다. 저장소 선행 정본과 동일한 ephemeral `--with pytest==9.0.3` 및 공식 Desktop `services/api/src` PYTHONPATH를 명령 한정으로 적용해 `5 passed`를 확보했다. 파일/lock/dependency는 변경하지 않았다.
- 정적 검증: Desktop lint `4 files` PASS, 소유 Rust `rustfmt --check` exit 0, `git diff --check` exit 0. 금지 scan은 Logging/Tauri Command/NEXT_PUBLIC/Docker 주소 0건이며, `PUBLIC_GATEWAY`·Bearer/Authorization은 Rust 내부 고정 Client에만, `127.0.0.1` 생성자는 `contract-test`에만 존재한다. `access_token`/`refresh_token` 문자열은 Safe Projection denylist에만 존재한다.
- 범위/Dirty: C09 추적 수정 3건(`native_session.rs`, `recovery_bridge.rs`, `recovery_bridge_contract.rs`), C09 문서 2건. 사용자 삭제 31건과 기존 미추적 3건 보존. 최종 `gen` 없음, Cargo/Rustc 잔존 프로세스 0건. Cargo/Cargo.lock·Python 제품/API·OpenAPI·Web/React·Local Service 제품은 변경하지 않았다.
- 금지 행위: Commit·Push·배포·Browser·실제 설치·운영/실제 Restore 0건.
- 판정: 자동 코드/계약 검증 범위 `COMPLETED`. 실제 Windows 설치형 Cloud 화면·운영 Restore PASS는 주장하지 않는다.

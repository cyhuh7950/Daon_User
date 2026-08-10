# R1-M5-07 Windows Native Session 2차 보안 보정 완료 보고

status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단
COMPLETED | R1-M5-07-WINDOWS-NATIVE-SESSION-C04-I001 | C05의 유효 보정을 보존하면서 RFC3339 검증, 제품 `NativeSessionRuntime` single-flight, logout revoke fail-close, 실제 reqwest Transport, Request·Wire·Buffer 취소/오류 Drop zeroize를 C06 승인 경계 안에서 보정했다. | `time 0.3.51` 직접 Pin과 RFC3339 UTC parser를 적용했다. 제품 Runtime은 gate 진입 전 generation ticket으로 같은 도착 세대 Refresh를 HTTP 1회로 합치고 새 Vault Projection을 모든 대기 호출에 반환하며 후속 새 세대 호출은 별도 1회 허용한다. logout revoke 실패는 pending 상태로 전환해 status를 fail-close하고 다음 logout revoke 성공 뒤 Target 부재를 확인한다. Production `fixed()`와 분리된 Loopback contract constructor로 실제 reqwest를 검증하며, Login·Refresh Request, 응답 Buffer, Wire Credential은 Drop에서 zeroize한다. Static 계약은 test-only Loopback 구간을 명시적으로 분리하고 Production Source의 localhost·내부주소 금지를 유지한다. | RED: RFC3339 잘못된 월 값 수용 0/1 FAIL, 제품 Runtime 주입 경계 부재 `E0599`를 확인했다. GREEN: 최종 격리 Wrapper에서 lib 16/16, Local Service 4/4, Native Session 19/19, 합계 39/39 PASS. 실제 reqwest 307/308 destination hit 0·Secret 전달 0, request timeout, chunked 128 KiB 초과, malformed Content-Length, truncated response fail-close와 Login/Refresh send 취소·transport 오류·Wire 조기 실패 Drop Guard를 통과했다. Desktop lint, `git diff --check`, 소유 Rust `rustfmt --check`, Production Native 금지주소/환경 패턴 0, WebView Credential field 0, Local Service diff 0을 확인했다. Node 정적 test는 C06 Native 보안 항목 포함 12/13 PASS다. | 기존 `node_modules`가 Next/Vite/PostCSS를 extraneous로 보고하는 기존 PostCSS 환경 test 1건은 C06 허용 범위 밖이라 수정하지 않았다. C05 공개 `NativeRefreshFlow` 삭제은 공개 API 제거 위험으로 진행하지 않았고, C06 지시의 허용 대안대로 신규 핵심 사례를 모두 제품 `NativeSessionRuntime`에서 직접 검증했다. 실제 Password·운영 Credential·Browser·설치형 실행·Commit·Push·배포는 금지 범위로 수행하지 않았다. 자동 테스트 결과는 실제 설치형 로그인 PASS가 아니다. | 어울1이 C06 diff와 보안 경계를 검토하고 Commit/Push 여부 및 후속 Windows Recovery Bridge 진행을 판단한다.

## 변경·보존 근거

- 변경 소유 파일: `apps/desktop/src-tauri/Cargo.toml`, `Cargo.lock`, `src/lib.rs`, `src/native_session.rs`, `tests/native_session_contract.rs`, `scripts/run-isolated-desktop-cargo.mjs`, `scripts/tests/desktop-tauri-shell.test.mjs`, C05/C06 progress·completion 문서.
- C06 핵심 구현 파일은 `src/native_session.rs`, `tests/native_session_contract.rs`, `scripts/tests/desktop-tauri-shell.test.mjs`와 C06 progress/completion이다. 나머지는 인수한 C05 유효 결과를 보존했다.
- 공식 기준선: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, `master`, HEAD `7f05aae3170388548878b489f03477e632bcb688`.
- 사용자 추적 삭제 31건과 사용자 미추적 문서 3건을 보존했다. 전체 미추적 6건은 C05/C06 보고 3건과 사용자 문서 3건이다.
- `apps/desktop/src-tauri/src/local_service.rs` diff 0건, Tauri `gen` 0건, C06 임시 Cargo Target 0건, Cargo/Rustc 잔여 0건을 확인했다.
- Commit·Push·배포·Browser·실제 Credential 사용은 수행하지 않았다.

## 2026-08-10 어울1 직접 구현 인수 시 정정

- 위의 `전체 미추적 6건`은 당시 실제 상태와 불일치했다. C05/C06 보고 4건과 사용자 문서 3건으로 합계 7건이었다.
- 이 정정은 과거 C06 판정을 변경하지 않으며, C07 직접 구현의 보존 기준만 정확히 바로잡는다.

# R1-M5-07 Windows Cloud Recovery Native Port C09 완료 보고

status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단
--- | --- | --- | --- | --- | --- | ---
COMPLETED | R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-I001 | 승인 설계·Task 4·OpenAPI Cloud Recovery 7 Path와 C07/C08 기준선을 확인하고 TDD 행동 RED→최소 GREEN→보안 회귀 순서로 Windows Rust Cloud Recovery Port를 구현했다. 사용자 삭제 31건과 기존 미추적 문서 3건, Native Session·Local Recovery·Web/CSP를 보존했다. | 정확한 Cloud 7 Method/Path·Query·Body allowlist, write별 Idempotency-Key, Execute/Cancel If-Match, Preview/Execute 별도 Step-up, write 무재실행과 GET 인증회전 후 1회 재시도를 구현했다. `NativeSessionRuntime`은 Vault Access를 반환하지 않고 Rust 내부 실행 경계에서만 전달한다. `NativeCloudRecoveryClient`는 고정 공개 HTTPS Gateway, redirect none, timeout, Bearer/Accept/JSON header, content length·type·실제 크기, Cookie·Chunked·Truncated·Oversize fail-close를 적용한다. Request/Response/credential 보조 buffer는 Drop·취소·오류에서 zeroize하며 Debug/Error/Safe Projection에 Header·Token·URL·Body 원문을 반환하지 않는다. | 행동 RED: 불법 추가 query가 transport까지 통과해 1 FAIL, Unknown·불완전 write body가 통과해 1 FAIL. 최소 수정 후 표적 Cloud Port 6/6, Actual Transport 2/2, 전체 Recovery Bridge 24/24 PASS. 최종 fresh `node scripts/run-isolated-desktop-cargo.mjs test`: Rust lib 18 + Local Service 5 + Native Session 19 + Recovery Bridge 24 = 66/66 PASS. API Recovery 5/5 PASS, Desktop lint 4 files PASS, rustfmt-check·`git diff --check` PASS. | 자동 Rust·Python·정적 계약 검증이며 실제 Windows 설치형 Cloud Recovery 화면 또는 운영 Restore PASS가 아니다. 지정 isolated API 명령은 저장소 환경에 pytest가 없어 ephemeral pytest와 공식 Desktop PYTHONPATH를 명령 한정으로 사용했다. Commit·Push·배포·Browser·실제 설치·운영 Restore는 수행하지 않았다. | 어울1이 Diff와 증거를 검토해 C09 기술 수락 여부를 판단하고, 후속 Task 5에서 Tauri Command/Windows React Adapter 연결 및 설치형 실제 검증을 별도로 수행해야 한다.

## 변경 파일

- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/native_session.rs`
- `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09_completion_report.md`

## 판정 이유

- 제품 생성자는 `PUBLIC_GATEWAY` 외 주소를 거부하며, Loopback 생성자는 `contract-test`에서만 컴파일된다.
- Cloud 7종 밖 Method/Path, 추가 Query, Unknown·불완전 Body, 누락·재사용 Header/Step-up, 위험 응답은 transport 전 또는 Projection 전 fail-close한다.
- 상태변경 요청은 인증 오류·4xx/5xx에서 자동 재실행하지 않고 GET만 인증회전 뒤 정확히 1회 재시도한다.
- Credential 원문 반환 Public 함수·Tauri Command를 추가하지 않았고, Vault Access는 Rust 내부 Bearer Header 경계 밖으로 전달되지 않는다.

## 조치 및 판정 경계

- 어울1 검토 전 Commit·Push를 하지 않는다.
- Cloud Tauri Command·JavaScript Adapter·화면 연결은 Task 5 범위로 남긴다.
- 실제 사용자 Credential·운영 데이터·DB·Backup을 사용하거나 변경하지 않았다.
- 실제 설치형·Browser·운영 Restore 검증 전에는 R1-WIN-01 또는 M5 Exit PASS를 주장하지 않는다.

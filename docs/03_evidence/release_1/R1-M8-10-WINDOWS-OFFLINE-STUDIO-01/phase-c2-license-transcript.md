# Phase C 메뉴 2 License actual Gate

- 실행일: 2026-08-15 ~ 2026-08-16
- Issue: `R1-M8-10-WINDOWS-OFFLINE-STUDIO-01-I001`
- 기준선: branch `codex/user-auth-screen-split`, HEAD `2d4c59e1c761ec12848dcfac8c2f04078dcbb47b`
- 범위: License Domain, PostgreSQL 0019, Runtime/OpenAPI, same-origin BFF, Web/Windows UI

## PostgreSQL actual

- WSL `local-postgres`의 고유 disposable DB·비슈퍼유저 `NOBYPASSRLS` role만 사용했다.
- fresh `0001→0019`, 빈 `0019→0018` rollback, `0018→0019` reapply를 통과했다.
- 최초 apply 1건, 같은 Idempotency-Key replay, changed fingerprint write0, durable Audit 1건을 확인했다.
- 다른 Tenant read0, cross-tenant `INSERT … SELECT` rowcount0, 기존 License UPDATE 차단을 확인했다.
- License table에 document/signature/private_key/claims column은 0이며, live row가 있으면 downgrade는 `LICENSE_DOWNGRADE_BLOCKED`로 fail-close했다.
- disposable DB·role remaining0, 기존 DB·role·data 변경0이다.

## Browser actual 1920×1080

- production `ProductWorkspaceShell`과 production `license-api`를 재사용하는 login-free 명시적 evidence harness를 사용했다. 제품 entry와 package에는 연결하지 않았다.
- 일반 사용자: 제품/Edition/마스킹 License ID/발급·만료/기능/한도·사용량·잔여를 표시하고 apply control0을 확인했다.
- 조직 관리자: License document file control, 현재 비밀번호, `Step-up 후 검증·적용` control을 확인했다. 실제 비밀번호·License document 입력/전송은 0이다.
- 만료: 신규 생성 중단과 기존 조회·Export 허용 문구를 확인했다.
- 한도 도달: 생성 실행 신규 생성 중단과 잔여0 경고를 확인했다.
- 이 최초 Browser 세션의 GET 1건 기록은 뒤의 독립 리뷰 재검증에서 4상태 actual Network로 대체되었으며 최종 PASS 근거에서 폐기했다. 최종 Network 판정은 `phase-c2-license-network-rework.json`의 same-origin GET 4건만 사용한다.
- Console에서 확인된 제품 오류는 0이며, screenshot 4장을 저장했다.

## Windows/Desktop

- Windows Rust `cargo check`와 guarded full contract tests 114건, Desktop Vite production build를 통과했다.
- production shared shell과 Windows native adapter 경계를 사용하는 login-free 명시적 contract-test Tauri 앱을 `Daon License C2 Evidence Final` 1920×1080으로 실제 실행했다. 제품 entry와 package에는 연결하지 않았다.
- 최초 `cargo run` dev profile에서 `frontendDist`가 serve되지 않아 WebView body가 `asset not found: index.html`인 evidence harness 조립 RED를 확인했다. 제품 control 결함이 아니므로 loopback Vite `devUrl`을 Tauri test config에만 연결해 교정했고, 제품 코드·공개 계약 변경은 0이다.
- 실제 Windows 창을 활성화한 뒤 Enter와 F8/F9 키로 설정 Popup·라이선스 메뉴 및 admin/expired fixture 상태를 전환했다. read-only는 초기 상태로 확인했다.
- read-only screenshot은 file/password/apply control `0/0/0`, 일반 사용자 읽기 전용 안내를 확인했다.
- admin screenshot은 file/password/apply control `1/1/1`을 확인했다. License document와 비밀번호 값은 입력하지 않았고 apply transport 호출은 0이다.
- expired screenshot은 `만료`, 신규 생성 중단, 기존 자료 조회·Export 계속 허용 문구와 apply control0을 확인했다.
- CDP projection은 세 상태 모두 outer/viewport `1920×1080`을 반환했고 screenshot bytes는 read-only `93746`, admin `98473`, expired `98434`였다.
- 저장 증거는 `phase-c2-license-desktop-readonly-1920x1080.png`, `phase-c2-license-desktop-admin-1920x1080.png`, `phase-c2-license-desktop-expired-1920x1080.png`이다.
- evidence Tauri/Cargo/Vite와 고유 temp target/dist/log를 exact cleanup했다. CDP9346·Vite4199 reachable0, target process remaining0이며 기존 제품·사용자 process는 변경하지 않았다.

## Secret boundary

- production public-key reference만 구현했다. Private signing key 생성·저장·추측은 0이다.
- test-only signature fixture는 test process memory에서 ephemeral RSA key를 생성하고 파일·로그·Evidence에 기록하지 않는다.
- Browser/Desktop bundle·로그·Evidence에 License 원문, signature, credential, password, internal claims digest와 signing key 판정정보는 0이다.

## 2026-08-16 독립 리뷰 재검증

- RS256 검증은 signature integer가 modulus 이상인 malleable encoding을 `LICENSE_SIGNATURE_INVALID`로 거부한다. ephemeral test key는 process memory에서만 사용했다.
- 동일 Idempotency-Key·동일 fingerprint 성공 replay를 서명·기간·Step-up 검증 전에 조회하므로 key 회수·License 만료 뒤에도 저장 결과를 replay한다. 다른 fingerprint는 `IDEMPOTENCY_KEY_REUSED`, write0, Audit 추가0이다.
- `studio.generate`는 `studio_generation`과 `generation_runs`·`studio_outputs`, `source.create`는 `citation`과 `source_versions`·`storage_bytes`로 매핑했다. 검사는 실제 생성 transaction 안에서 tenant advisory lock 후 수행되고 성공한 생성 row와 함께 commit된다. 기존 조회·Export는 이 경계를 호출하지 않는다.
- actual PostgreSQL 15의 두 연결에서 같은 fingerprint apply는 최초1/replay1, 다른 fingerprint apply는 저장1/conflict1이었다. `source_versions=1`과 `storage_bytes=1024` 한도 경쟁은 각각 저장1/limit1, 최종 row1/bytes800이며 부분 소비0이었다. non-superuser FORCE RLS, live-row downgrade block, disposable DB·role cleanup `0/0`을 재확인했다.
- Web JS, Desktop JS, Rust bridge는 OpenAPI의 상태·기능 ID·마스킹 ID·UTC timestamp·resource enum/remaining/status·warning enum을 fail-close한다. Rust full contract는 115건 PASS다.
- 기존 Browser 캡처가 실제 `1920×1071`이었음을 독립 리뷰에서 확인해 PASS 근거로 사용하지 않았다. Chrome 151 CDP `Emulation.setDeviceMetricsOverride`로 viewport `1920×1080`을 고정하고 read-only/admin/expired/limit 네 상태를 실제 재캡처했다.
- 새 Browser bytes/SHA-256: read-only `92456`/`F30F0F84…089BE`, admin `97444`/`433CA5C3…327C2`, expired `97350`/`01170EDE…075D3`, limit `97299`/`17BE91E7…A7D31`. read-only controls `0/0/0`, admin `1/1/1`이며 actual same-origin GET 4건은 `phase-c2-license-network-rework.json`에 기록했다.
- Chrome CDP9347·Vite4179 listener0, 생성한 Rust temp crate remaining0, License document/password/apply transport0이다.

## 2026-08-16 2차 독립 리뷰 재검증

- License Idempotency fingerprint는 tenant·workspace와 canonical 전체 envelope(`schema_version`, `key_id`, `algorithm`, `claims`, `signature`)를 결속한다. 최초 apply와 선행 replay가 동일 함수를 사용하며, 동일 claims라도 key/algorithm/signature/workspace 변경은 `IDEMPOTENCY_KEY_REUSED`, write0이다.
- Runtime은 exact `StudioWorkspaceService`/`StudioReportService`가 PostgreSQL creation enforcer를 가진 경우에만 별도 선행 License 검사를 생략한다. 이 경로는 PostgreSQL transaction의 replay→tenant license lock/check→insert 순서를 사용하며, 임의 Fake와 non-PG service는 기존 선행 fail-close를 유지한다.
- actual PostgreSQL에서 최초 Studio report 생성 후 generation/output 한도가 1에 도달한 상태와 최신 License가 만료된 상태 모두 같은 Idempotency-Key replay는 기존 `output_version_id`를 반환하고 write0이었다. 새 key는 각각 `LICENSE_RESOURCE_LIMIT_REACHED`, `LICENSE_EXPIRED`로 차단됐고 StudioOutput/Audit는 각1이다.
- `storage_bytes`는 projection과 enforcement 모두 `pending|completed` object bytes를 사용한다. actual 두 연결 800-byte 경쟁은 저장1/limit1이었고 projection은 used800/remaining224/available/creation_allowed true로 enforcement와 일치했다.
- fresh migration0019→rollback0018→reapply0019, non-superuser FORCE RLS, 전체 actual PG 3 PASS, disposable DB/role cleanup `0/0`이다.
- 최초 Browser 세션의 GET1은 최종 판정에서 폐기했으며 `phase-c2-license-network-rework.json`의 1920×1080 4상태 same-origin GET4만 최종 근거로 사용한다.

## 2026-08-16 3차 독립 리뷰 재검증

- Runtime은 `creation_license_authoritative=True`라는 duck-typed 속성만 신뢰하지 않는다. exact `StudioWorkspaceService`+exact `PostgresStudioWorkspaceRepository` 또는 exact `StudioReportService`+exact `PostgresStudioReportRepository`이고 실제 creation enforcer가 있을 때만 선행 License 검사를 생략한다.
- 같은 속성을 가진 Fake repository를 실제 `StudioWorkspaceService`로 감싼 RED는 기존 helper에서 precheck를 잘못 생략했다. exact trusted implementation 검사로 교정한 뒤 Fake/non-PG는 precheck true, trusted PostgreSQL Workspace/Report만 false다.
- 공개 API·OpenAPI·데이터·DB·UI 계약 변경은 0이다. focused `46 PASS·1 SKIP`, API 전체 `431 PASS·32 SKIP·137 subtests`를 fresh 재검증했다.

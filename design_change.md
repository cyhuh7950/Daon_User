# 설계 변경·미진사항 보조 기록부

이 파일은 신산님의 2026-10-09 12:05 지시와 12:06 파일명 정정에 따른 보조 기록부다. 12:35·12:38 직접 지시에 따라 **실제로 진도를 더 낼 수 없는 개별 항목**의 시도·원인·영향·미충족 조건·재개 조건을 여기에 기록하면 그 항목은 이번 작업계획에서 기록으로 처리하고 다음 실행 가능 항목으로 진행한다. 단순 미실행·미검증은 진행 불가가 아니며, 아래 광범위한 미진 목록 전체를 일괄 완료로 보지 않는다. 승인된 상세 설계 `docs/daon-user-program-design.md`, 작업계획 `docs/daon_user_program_development_plan.md`, 진행 현황 `docs/WORK_STATUS.md`를 대체하지 않는다. 기록에 따른 처리는 기능 구현·실경로 검증 PASS, Release/사용자 인수, 설계·권한·DB·배포 변경 승인과 구분한다.

## 2026-10-09 C9 §30.3 R5 / §30.4 — de73 WSL QA 잔여 검증

- 사실: 정식 WSL QA API는 정확한 `de73b49c85ff900e273d47f117bd6b4cd82b6e32` image로 교체됐고 healthy/ready 200, Web 3330 200, DB revision 0056을 확인했다. Web·worker·storage는 불변이다. 상세 증거는 `docs/WORK_STATUS.md`의 C9 de73 기록에 있다.
- 미진/영향: `de73` 이미지에서 실제 중앙 감사 장애→주기 재전달은 미검증이다. 읽기 전용 조회에서 미전송 outbox 0건이므로 기본 smoke만으로 재시도를 PASS로 판정할 수 없다. 앞선 C9 브라우저 조직 선택·역할 축소·개인 복귀 PASS는 이전 API image에서 얻었으며 새 image의 재실행 증거가 아니다.
- 별도 경계: 정식 공유 QA DB에 감사 장애나 QA 역할·세션을 의도적으로 만드는 시험은 대상·영향·복구 조건을 먼저 확정해야 한다. Windows Native 설치판 HTTPS 실경로, Oracle 및 사용자 인수는 별도 검증·승인 범위다.
- 권고/다음 조치: 현재 승인 범위 안의 비파괴적 exact-SHA 회귀와 Web/BFF 경로를 먼저 확인한다. 통제된 실패·복구 시험이 필요하면 정식 공유 DB/권한을 건드리지 않는 격리 환경에서 재현하고, 정식 실경로가 필수라면 정확한 QA 대상·일시적 영향·복구 방법을 별도 판정한다. 각 결과는 `WORK_STATUS`에 PASS/FAIL/UNVERIFIED를 분리해 기록한다.
- 안전한 해결 시도: 현재 de73 브랜치에서 `test_identity_admin_runtime.py` 전체 6 PASS, `session-tenant-bff.test.mjs`와 `session-tenant-web.test.mjs` 합계 6 PASS, Web production build/TypeScript 17 pages·UI boundary 479파일/위반 0을 재확인했다. 이는 로컬 계약/빌드이며 WSL 중앙 감사 장애 재시도·인증 브라우저 E2E는 여전히 UNVERIFIED다.
- 브라우저 도구 장애: Browser Use `getState()`가 Windows sandbox `helper_unknown_error: apply deny-read ACLs`로 종료되어 kernel reset 후 한 번 재시도했으나 동일 오류였다. 브라우저 입력/QA 사용자·DB 변경은 0. 이 도구 장애는 제품 실패가 아니며, 새 API image의 실제 브라우저 E2E는 여전히 UNVERIFIED다. 다른 안전한 브라우저 시험 경로는 기존 QA 자료·인증 제한과 자원 소유를 확인한 뒤 진행한다.
- Native 계약 재확인: 현재 C9 브랜치의 `test_identity_sessions.py`는 5 PASS/2 SKIP/0 FAIL이다. SKIP 2건은 PostgreSQL opt-in 환경 부재로 이번 실행의 PASS가 아니다. 이전 격리 0056 PostgreSQL 2 PASS는 별도 증거이며 Windows 설치판/운영 HTTPS 실경로를 증명하지 않는다.
- 정적 Gate: 현재 C9 브랜치에서 `npm run lint:workspace` 16파일 PASS/exit 0. 제품 코드 수정은 없으며 이 결과는 공유 QA 실경로 검증을 대신하지 않는다.
- 관리자 UI/BFF 독립 회귀: `admin-user-management-react`, `admin-user-management-ui`, `admin-user-idempotency-key` 합계 40 PASS/0 FAIL, `admin-users-bff`와 `organization-admin-role-label` 합계 8 PASS/0 FAIL. 이는 같은 SHA의 로컬 계약 증거이며 정식 QA 계정의 실제 브라우저 역할 변경 재실행은 아니다.

## 2026-10-09 C9 §30.3 R5 / §30.4 — 남은 실경로 결정 경계

- 재현 증거: `de73` WSL API image는 healthy/restart 0, Web 3330 기본 200, DB 0056, 감사 미전송 outbox 0건이다. Browser Use 초기화가 Windows ACL 오류로 2회 실패해 새 image의 UI/Network를 관찰하지 못했다. 이전 API image의 headless Chrome 조직 선택·역할 축소 후 403·개인 복귀는 `WORK_STATUS`에 있지만 현재 image의 재실행으로 계산하지 않는다.
- 이미 시도한 안전한 해결: 현재 코드에서 API 인증 런타임 6 PASS, Native session 5 PASS/2 PostgreSQL opt-in SKIP, Web/BFF 6 PASS, 관리자 UI 40 PASS·관리자 BFF/역할 안내 8 PASS, Web build/TypeScript/경계 및 lint PASS. 이들은 브라우저/중앙 감사 장애 실제 복구를 대신하지 않는다.
- 영향/남은 조건: 계획 §30.3 R5·§30.4는 정식 QA 사용자 개인→조직→역할 축소 후 구 세션 거부→개인 복귀의 현재 image 브라우저/Network·감사와 잔여 Gate를 요구한다. 이번 지시로 계획상 **처리**는 마칠 수 있지만 이 실증 없이 Stage PR 적격성·제품/Release 검증 완료를 선언할 수는 없다.
- 대안 A(권고): 앞서 사용한 정확한 QA 관리자·대상만 다시 한시적으로 사용해 QA 자격증명 재발급과 단일 관리자 allowlist overlay를 수행하고, 설치된 headless Chrome CDP 경로로 현재 image에서 재시험한다. 역할은 공식 관리자 API `CORRECTION`으로 원래 값에 복귀하고 두 QA session을 revoke, allowlist·image/config hash·DB revision·임시 프로필 잔여를 확인한다. 공유 QA 인증/권한·감사 이력·role version 증가와 일시적 API 재생성이 영향이다. 직전 API-only 승인에 이 재시험 권한이 포함되는지 별도 판단이 필요하다.
- 대안 B: 공유 QA 자격증명/권한을 바꾸지 않고 현 로컬 계약과 기본 smoke만 유지한다. 운영 영향은 없지만 현재 image 브라우저 Gate와 Stage PR은 UNVERIFIED/보류다.
- 중앙 감사 장애→재시도: 현재 outbox 0건이므로 정식 공유 환경에서 인위 장애/행 주입은 API-only 승인 밖이다. 우선 격리 합성 환경의 실패→복구 계약 증거를 유지하고, 정식 실경로가 필수라면 별도 대상·일시적 영향·복구 승인 후 수행한다.

## 2026-10-09 작업계획 미진 추적 목록 — 실행 가능성 재판정 중

아래 항목은 정본 계획 §9.1·§29·§30~33과 `WORK_STATUS`의 증거 수준을 대조한 **추적 목록**이다. 미실행·미검증이라는 이유만으로 처리 완료하지 않는다. M3~M8·C3~C8의 독립 실행 가능 부분은 계획 작업을 계속하고, 실제 승인·환경 경계가 확인된 개별 항목만 구체적 장애 기록으로 분리한다. 이미 구현된 부분은 보존하며 다음 사이클의 새 설계·계획은 신산님의 별도 지시와 각 승인 경계를 따른다.

| Stage/Task | 확인된 상태와 진도 제한 | 영향·남은 조건 / 다음 사이클 조치 |
| --- | --- | --- |
| M3 / R1-M3-01~06, C1~C2 | 2026-10-09 현재 C9 브랜치에서 Web/Mobile Shell 계약 91건 재실행: 81 PASS·10 FAIL. iOS 8건은 현재 브랜치에 없는 `.github/workflows/release-1-ios-phase-a.yml`을 요구하고, Web 2건은 현 layout의 `WebShellRuntimeStatus` 부재와 M2 파일 hash 변경으로 실패했다. 설치 Windows WebView·오프라인 복구와 Android/iOS 실기기 전체 여정 증거도 없다. | 전체 M3는 **진행 중/FAIL**, 일괄 기록 완료가 아니다. 실패 10건은 현행 설계와 삭제 이력(`3f42fdfe`) 대조 후 승인된 Stage의 단일 writer가 시험·제품 계약을 정합화해야 한다. 현 C9 미종결 브랜치에서 다른 Stage 코드로 섞지 않는다. 실제 장치·서명·오프라인→재연결은 별도 QA. |
| M4 / R1-M4-01~07, C1·C9 | API/BFF·인증·권한 계약 및 C9 로컬/WSL 일부 실증은 있다. 현 `de73` 인증 브라우저 역할 축소와 전체 역할·세션 행렬은 미재현이다. | 권한 누출 0·Native/브라우저 실제 흐름·복구 Gate는 UNVERIFIED. 기존 QA 계정의 한시 권한·원복 범위를 확정한 뒤 정확 SHA로 재시험한다. |
| M5 / R1-M5-01~07, C2·C4 | PostgreSQL/Local 계약과 격리 migration 시험은 있으나 Windows Backup/Restore·손상 복구·Sync 충돌/DR 전체 실측이 없다. | 데이터 복구 Exit는 UNVERIFIED/NO_GO. 실제 설치판·격리 복구 자원으로 무손실/rollback을 증명한다. 공유 DB 전체 복원은 이 기록으로 승인되지 않는다. |
| M6 / R1-M6-01~16, C6~C8 | Provider/Router·검색/Connector 계약과 일부 WSL 시험은 있으나 M6 Evidence Manifest, 실제 전체 Provider·Source·권한/계보 여정 및 C8 개인 Key/AAD 독립 Gate가 남았다. | 실제 비용·Key/모델·외부 Source의 운영 PASS는 UNVERIFIED. 허가 QA Key·비용 상한·정확 SHA로 경로별 검증하고 미해결 Important를 재판정한다. |
| M7 / R1-M7-01~06, C4~C5 | Web Source→질문→Citation 일부 증거가 있으나 Windows·Mobile의 실제 파일·권한·오류·cleanup 포함 전체 수직 E2E는 없다. | 전 Client 여정은 UNVERIFIED. 동일 데이터/근거 버전과 QA 계정으로 클라이언트별 실제 파일을 추적한다. |
| M8 / R1-M8-01~13, C5 | 구조화 Studio 일부 실제 Job은 있으나 다섯 산출물의 실제 파일 열기·버전·감사·Export·승인·전달, 오디오/동영상 및 M8-09/10 미진이 남았다. | Studio 전체 수명주기 PASS는 불가. 각 산출물·Egress/Step-up·다운로드/복구를 별도 증거로 채운다. |
| M9 / R1-M9-01~10·V01~V02, C10 | 부분 배포·인증 QA만 있고 정식 Oracle staging·전체 rollback/DR·성능/접근성·독립 검증·G9-RELEASE·사용자 인수 증거가 없다. 매뉴얼 원본/게시본/manifest의 현행성도 불일치한다. | Release·운영 전환은 NO_GO/UNVERIFIED. 정확 SHA·QA 자료·rollback을 갖춘 승인된 staging/인수와 매뉴얼 동기화를 별도 Gate에서 수행한다. |
| C3 Evidence Hub, C6a Provider 템플릿 | 구조/일부 WSL 증거는 있으나 제품별 설치판 혼입 방지와 3330 관리자·일반 사용자 시각/권한 클릭 증거가 제한적이다. | 제품별 분리·템플릿 사용성 인수는 UNVERIFIED. 실제 Client별 UI/Network를 확인한다. |
| C4 Notebook 삭제·C5 Studio Egress·C6 Provider 운영 | C4의 격리 DB 객체/공유 참조 안전 계약과 C5의 일부 Studio Job 증거, C6의 Provider 설정·라우팅 계약은 있다. 실제 object-storage 바이트 삭제·공유 참조 최종 수명주기, Studio 5종의 승인/내보내기·Step-up, Provider 관리자 UI/Key 수명주기 전체 실경로는 한꺼번에 검증되지 않았다. | 격리 테스트 PASS를 실제 자료 삭제·권한·외부 전송 PASS로 확대하지 않는다. 정확 SHA·QA 계정/자료·비용/반출 경계에 맞춰 각 최종 사용 흐름을 별도 검증한다. |
| C7 호환 규격, C8 Router Auto | 규격/Router Auto 계약과 PR #39 병합은 확인됐으나 병합 자체는 실제 유료 Provider·개인 Key/AAD·실 브라우저 품질 Gate PASS가 아니다. | 비용·실 Provider·Key 보존/재입력·개인 범위는 별도 허가 후 검증한다. 미해결 Important가 있으면 PR·Release 승격 금지. |
| C9 §30.3 R5 / §30.4 | 관리자 역할·조직 전환 코드와 이전 image 브라우저 증거, `de73` 계약/빌드·WSL 기본 smoke는 있다. 현 image 인증 브라우저 재실행·중앙 감사 실제 장애 회복·Native 설치판은 미검증이고 계획 원문의 구현 대기 표기는 실제 코드 진도와 어긋난다. | 상세 장애·대안 A/B는 위 C9 기록을 따른다. 기술 Gate/PR/병합은 별도 판정하고 현재 미검증을 PASS로 바꾸지 않는다. |
| C11-01~05 | 사용 가능 Provider/모델 조회·개인 Key 우선·모델별 시험·추론 등급은 정본상 미구현/실행 승인 대기다. | 설계/권한/DB/비용/wire 계약의 새 승인 없이 선구현하지 않는다. Release 1 기능 PASS로 계산하지 않는다. |
| C12-01~05 | OpenTelemetry/Ragas는 정본상 문서 반영안·미착수이고 Release 1 필수 Gate에 미편입이다. | 허가 QA 자료·수치/비용·수집/보존·라이선스 승인이 필요한 별도 후속 Stage로 둔다. 이번 계획 처리 완료를 제품 구현 완료로 해석하지 않는다. |

### C9 미완료 체크의 처리 기준

정본 계획 §30.3 R5의 4개와 §30.4의 5개 미완료 체크는 위 C9 기록과 `WORK_STATUS`의 이미 수행한 로컬·격리 DB·이전 image Chrome·현 image 기본 smoke 증거로 **각각의 실행 가능성**을 분리한다. 현재-image 인증 브라우저/권한·중앙 감사 실제 장애 재시도처럼 별도 공유 QA 조작 승인이 필요한 부분만 기록으로 처리하고, 승인 범위 안의 독립 검증은 계속한다. forward recovery·PR, 승인 정합성 문서 drift, 실제 current-image 전환/Native·인수는 여전히 UNVERIFIED 또는 별도 승인 대기다. 따라서 원문 체크박스를 시험 PASS로 바꾸거나 PR/병합을 강행하지 않는다.

### M3 현재 실행의 정확한 실패·재개 조건

- 시도: 활성 C9 브랜치 `054d2206`에서 `node --test scripts/tests/web-runtime-shell.test.mjs scripts/tests/web-runtime-shell-hydration.test.mjs scripts/tests/mobile-shared-shell.test.mjs scripts/tests/android-native-shell.test.mjs scripts/tests/ios-native-shell.test.mjs` 실행, exit 1, 91개 중 81 PASS·10 FAIL. 제품 코드는 변경하지 않았다.
- 확인 원인: iOS 8건은 파일 부재 `ENOENT(.github/workflows/release-1-ios-phase-a.yml)`로 동일 실패하며 Git 이력 `3f42fdfe`에서 해당 workflow가 삭제됐다. Web 2건은 `apps/web/app/layout.jsx`에 시험이 기대한 `WebShellRuntimeStatus`가 없고, `packages/ui/src/operations-recovery-model.js`의 실제 SHA-256이 시험의 고정 hash와 다르다. 이것을 Windows/macOS 실행 환경의 실패나 iOS 실기기 결함으로 단정하지 않는다.
- 영향: M3 Web/iOS 계약 Gate 0 FAIL 조건 미충족. 현재 C9 Stage 미종결·미병합 브랜치 정책상 M3 구현 브랜치를 병렬 신설하거나 C9 PR에 M3 수정을 섞지 않는다. 다른 Stage의 비파괴적 독립 검증은 계속 가능하다.
- 재개: C9 Stage의 승인된 기술/PR 경계를 정리해 브랜치를 정상 종료하거나, 현재 브랜치와 분리된 M3 작업 브랜치 예외를 신산님이 직접 승인한 뒤, 설계·현재 UI 의도를 확인하고 실패를 RED 시험으로 삼아 최소 정합화·전체 회귀를 수행한다. 삭제된 workflow를 이유 없이 복원하거나 hash 기대값만 맹목적으로 바꾸지 않는다.

### M4~M8·C6~C8은 일괄 기록 완료가 아닌 계속 작업 대상

2026-10-09 활성 C9 코드 기준으로 기존 격리 Python 3.14.3과 임시 패키지 경로에서 M4 관리자 권한 계약 6 PASS, M5~M8의 Sync·Source·근거 질문·Studio Export 계약 8 PASS, C6~C8 Provider catalog·개인 credential·Adapter 계약 100 PASS를 확인했다. 시스템 Python/초기 격리 Python의 두 수집 오류는 패키지 경로 보정으로 해결했다. 이 실행은 위 추적표의 실제 브라우저·DB 복구·외부 Provider·장치·실파일·승인/반출을 증명하지 않는다. **해당 Stage는 미실행이라는 이유로 처리 완료하지 않았고**, 승인 범위의 독립 시험·기능 검증을 계속한다.

### M3 Windows 설치본 제작의 로컬 Rust 도구 부재

- 시도: 기존 C9 격리 worktree에서 Desktop Shell/Local Service Node 계약 48건 PASS 후 `npm run build:desktop-installer`를 1회 실행했다. Local Service sidecar 생성까지 진행했으나 Tauri가 `cargo metadata`를 호출할 때 `program not found`로 종료(exit 1)했다.
- 확인 원인·정리: 현재 Windows PATH의 `cargo`가 없고 기본 사용자 Cargo 위치에도 실행 파일이 없다. wrapper가 실패한 임시 `daon-user-desktop-installer-*` target과 생성 sidecar를 정리했고, Tauri `gen`도 남지 않았다. 빌드 과정에서 Cargo.toml의 작업트리 변경 표시가 생겼으나 blob이 원본과 일치함을 확인하고 인덱스 메타데이터를 새로고침해 tracked 변경 0으로 복귀했다. 기존 미추적 `.pytest-tmp/`는 보존했다.
- 영향·재개: 이 Windows 환경에서는 NSIS 설치본 생성·설치 실행 Gate를 판정할 수 없다. 승인된 Rust toolchain과 Windows 빌드 전제조건을 갖춘 독립 환경에서 정확 SHA로 다시 제작하고, 생성된 EXE/NSIS 서명·설치·WebView/Local Service 실제 클릭을 별도 확인해야 한다. 48건 계약 PASS를 설치판 PASS로 바꾸지 않는다. 다른 독립 계획 검증은 계속한다.

### C6a 1920×1080 배치 시험 실행기의 측정 출력 부재

- 시도·증거: C9 브랜치에서 Provider 화면 계약 4파일 37건 실행, 35 PASS·2 FAIL. 실패는 모델/Provider 목록의 실제 크기·스크롤 값이 아닌 `--headless --dump-dom`의 `<output id="result">` 미출력이다. 설치된 Edge와 Chrome 각각에 최소 `data:` DOM을 출력하도록 시도해도 exit 0·stdout 빈 값이었다.
- 영향·판정: 현 명령으로는 1920×1080 내부 스크롤을 판정할 수 없다. 이는 **시험 실행기 측정 미완료**이며 제품 레이아웃의 PASS/FAIL이 아니다. 나머지 Provider 설정 계약 35건의 PASS만 유지한다.
- 재개 조건: 별도 브라우저 세션·CDP로 DOM 크기를 직접 읽고, 모델 목록/Provider 목록의 고정 박스·내부 스크롤·페이지 바깥 스크롤 0 조건을 같은 1920×1080 기준으로 재실행한다. 현재 C9의 인증 QA 세션·Key/권한은 변경하지 않는다. 이 도구 경계만 기록하고 다른 독립 작업을 계속한다.

### M9·C11·C12의 개별 선행·승인 Gate

| 항목 | 현재 시도·확인된 중단 원인 | 영향·재개 조건 |
| --- | --- | --- |
| R1-M9-01~10, V01~V02 | 정본 계획 §19의 M9-01은 M8-13, M9-03~10은 선행 M9 작업·M5-07이 필요하다. 현재 M3 Web/iOS 계약 10 FAIL, M8 전체 5종 수명주기와 M9 선행 Gate 미완료이므로 이번 턴에 정식 G9 체인을 실행할 입력이 없다. WSL C9 API 기본 health와 Web 200은 M9 선행 충족이 아니다. | **M9 Release 검증은 NO_GO**. 해당 선행 Task의 실제 제품·시험 증거를 먼저 채우고, 외부 접근 배포는 G9-DEPLOY, 운영 대상 DR 훈련은 G9-DRILL, Oracle 인수는 별도 승인 후 정확 SHA로 진행한다. 단순 미착수 전체를 기록 완료로 계산하지 않는다. |
| C11-01~05 | 정본 계획 §31은 상세설계 §36 초안을 실행 승인·구현 완료로 보지 않으며, 사용자 Key 우선/권한·DB migration·공개 API·비용/wire 계약을 별도 Gate로 둔다. 현 사용자 화면·Key/추론 등급 코드는 미구현이다. | **설계/정책/DB/비용 승인 전 선구현 금지**. 결정이 확정되면 C9 브랜치 정리 또는 예외 승인 뒤 별도 Stage·검증 경계에서 구현한다. 기록은 새 권한·비용 승인을 만들지 않는다. |
| C12-01~05 | 정본 계획 §33은 독립 후속 Stage를 `PLANNED / NOT_STARTED / EXECUTION_APPROVAL_PENDING`으로 고정한다. 허가 QA 원본·접근/보존·표본군 분모·품질/지연/비용 기준선, collector/SDK와 외부 평가 모델·전송/비용 결정이 없다. 앞선 합성 fixture 시험은 실제 품질 평가가 아니다. | 실제 자료 추출·OpenTelemetry collector 설치·Ragas 유료/외부 호출·제품 경로 변경을 시행하지 않는다. 필요한 자료/수치/보안/비용 승인을 각각 받고, 세 표본군의 실제 baseline 확보 후 독립 Stage를 시작한다. 이번 Release 1 기술 PASS에 포함하지 않는다. |

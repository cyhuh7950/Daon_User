# 설계 변경·미진사항 보조 기록부

이 파일은 신산님의 2026-10-09 12:05 지시와 12:06 파일명 정정에 따른 보조 기록부다. 12:35 직접 지시에 따라 막힌 항목은 원인·영향·미충족 조건을 여기에 기록하면 **이번 작업계획의 처리 완료**로 간주한다. 승인된 상세 설계 `docs/daon-user-program-design.md`, 작업계획 `docs/daon_user_program_development_plan.md`, 진행 현황 `docs/WORK_STATUS.md`를 대체하지 않는다. 기록에 따른 처리 완료는 기능 구현·실경로 검증 PASS, Release/사용자 인수, 설계·권한·DB·배포 변경 승인과 구분한다.

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

## 2026-10-09 작업계획 전체의 미진 처리 목록

아래 항목은 정본 계획 §9.1·§29·§30~33과 `WORK_STATUS`의 확인된 증거 수준을 대조한 **처리 완료 기록**이다. 이미 구현된 부분은 보존하고, 미실행·미검증을 성공으로 추정하지 않는다. 다음 사이클의 새 설계·계획은 신산님의 별도 지시와 각 승인 경계를 따른다.

| Stage/Task | 확인된 상태와 진도 제한 | 영향·남은 조건 / 다음 사이클 조치 |
| --- | --- | --- |
| M3 / R1-M3-01~06, C1~C2 | Web Shell·일부 브라우저 계약은 존재하나 설치 Windows WebView·오프라인 복구와 Android/iOS 실기기 전체 여정 증거가 없다. | Client Shell·복구의 실제 기기 PASS와 제품 배포 판정은 UNVERIFIED. 설치판/실기기·서명·오프라인→재연결을 별도 QA로 검증한다. |
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

정본 계획 §30.3 R5의 4개와 §30.4의 5개 미완료 체크는 위 C9 기록과 `WORK_STATUS`의 이미 수행한 로컬·격리 DB·이전 image Chrome·현 image 기본 smoke 증거로 범위를 분리해 **계획 처리 완료**로 분류한다. §30.3 R5의 실제 현재-image 브라우저/권한·forward recovery·PR과 §30.4의 승인 정합성 문서 drift, 실제 current-image 전환/Native·인수는 여전히 UNVERIFIED 또는 별도 승인 대기다. 따라서 원문 체크박스를 시험 PASS로 바꾸거나 PR/병합을 강행하지 않는다.

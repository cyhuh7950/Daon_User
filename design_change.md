# 설계 변경·미진사항 보조 기록부

이 파일은 신산님의 2026-10-09 12:05 지시와 12:06 파일명 정정에 따른 보조 기록부다. 승인된 상세 설계 `docs/daon-user-program-design.md`, 작업계획 `docs/daon_user_program_development_plan.md`, 진행 현황 `docs/WORK_STATUS.md`를 대체하지 않으며, 여기 적은 권고는 설계·권한·DB·배포 변경 승인이나 검증 PASS가 아니다.

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
- 영향/남은 조건: 계획 §30.3 R5·§30.4는 정식 QA 사용자 개인→조직→역할 축소 후 구 세션 거부→개인 복귀의 현재 image 브라우저/Network·감사와 잔여 Gate를 요구한다. 이를 생략하면 Stage PR 적격성·전체 완료를 선언할 수 없다.
- 대안 A(권고): 앞서 사용한 정확한 QA 관리자·대상만 다시 한시적으로 사용해 QA 자격증명 재발급과 단일 관리자 allowlist overlay를 수행하고, 설치된 headless Chrome CDP 경로로 현재 image에서 재시험한다. 역할은 공식 관리자 API `CORRECTION`으로 원래 값에 복귀하고 두 QA session을 revoke, allowlist·image/config hash·DB revision·임시 프로필 잔여를 확인한다. 공유 QA 인증/권한·감사 이력·role version 증가와 일시적 API 재생성이 영향이다. 직전 API-only 승인에 이 재시험 권한이 포함되는지 별도 판단이 필요하다.
- 대안 B: 공유 QA 자격증명/권한을 바꾸지 않고 현 로컬 계약과 기본 smoke만 유지한다. 운영 영향은 없지만 현재 image 브라우저 Gate와 Stage PR은 UNVERIFIED/보류다.
- 중앙 감사 장애→재시도: 현재 outbox 0건이므로 정식 공유 환경에서 인위 장애/행 주입은 API-only 승인 밖이다. 우선 격리 합성 환경의 실패→복구 계약 증거를 유지하고, 정식 실경로가 필수라면 별도 대상·일시적 영향·복구 승인 후 수행한다.

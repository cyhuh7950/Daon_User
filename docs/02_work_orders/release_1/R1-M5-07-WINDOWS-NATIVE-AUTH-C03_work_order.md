# R1-M5-07 Windows Native 로그인 기반 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WINDOWS-NATIVE-AUTH-C03` |
| issue_id | `R1-M5-07-WINDOWS-NATIVE-AUTH-C03-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 성격 | R1-D028 공개 API·Native Session 기반 구현 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-AUTH-C03_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-AUTH-C03_completion_report.md` |

## 2. 승인 정본

다음 문서를 EOF까지 읽고 경로·버전·SHA-256·승인 상태·적용 조항을 진행 기록에 남긴다.

| 문서 | SHA-256 |
| --- | --- |
| `AGENTS.md` | `AABB11177EA7541B62C0AD6E6AB2FD745FCD4ADED72A25DF98522FC8E41B47EA` |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6FF5E944C4C7BA66A73B82333A9172391B7ED96F2B532FABB7779BC28518F418` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `58B677D24D356499CB1891E4B5C5366657F9BC7A774E68E335016809A1CF8F31` |
| `docs/superpowers/specs/2026-08-10-windows-recovery-adapter-design.md` | `9AF48A42653CDC44F0674FECA407FA16F693E34ED180830608B28D5F1E6BBF38` |
| `docs/superpowers/plans/2026-08-10-windows-recovery-native-bridge.md` | `393F76D72B5A4A4E07A20477257D15EB1D5C47F5941A0E1DB6D14E4F5B97162D` |
| `docs/02_work_orders/approvals/APR-R1-M5-07-WINDOWS-NATIVE-LOGIN-20260810-01.md` | `F768EC82B6A4CFBCB9598B809F8CAA22F4583EA250AB49C2E09C0BBEEDE7541A` |
| `docs/04_test_reports/release_1_test_plan.md` | `CF607EE9CF25552F051BBC382EB269E5AE11C7C0E614C7C6D2223FE6DE7560F2` |

Hash·상태가 다르면 코드를 수정하지 말고 `INCOMPLETE`로 되돌린다. 본 작업은 승인 구현계획 Task 1만 수행한다.

## 3. 목표

기존 Web 로컬 로그인의 Secure·HttpOnly Cookie 계약을 변경하지 않고, Windows 설치형이 Rust Native 계층에서 사용할 별도 `POST /api/v1/auth/native/login`과 Native Access·Refresh 세션 발급 계약을 구현한다.

## 4. 구현 계약

1. `POST /api/v1/auth/login`은 Web 전용으로 유지하고 응답 Body에 Access·Refresh Token을 추가하지 않는다.
2. `POST /api/v1/auth/native/login` 요청은 `login_id`, `password`만 허용한다. `platform`, `client_kind`, Tenant, Workspace 입력은 Pydantic `extra=forbid`로 거부한다.
3. Server가 `DevicePlatform.WINDOWS`, `ClientKind.NATIVE`를 고정한다.
4. 기존 Password 검증·메일 인증·활성 사용자·Membership·Audit 경계를 재사용한다.
5. 성공 시 Native Device·Session·단일 사용 Refresh Family를 만들고 opaque Access·Refresh를 발급한다.
6. 응답은 `user_id`, `tenant_id`, `workspace_id`, `session_id`, `device_id`, `client_kind`, `delivery`, `access_credential`, `refresh_credential`, `expires_at`을 포함하며 Cookie는 0건이다. `client_kind=native`, `delivery=native_https_opaque_bearer`를 고정한다.
7. Refresh 회전·재사용 탐지·철회는 기존 `rotate_refresh` 계약을 변경하지 않고 재사용한다.
8. 자격 실패는 값이 없는 기존 Safe Error로 응답하고 Password·Token을 Log·Trace·Audit metadata에 기록하지 않는다.
9. OpenAPI는 새 Route·Request·Response를 정확히 기술하고 Credential Schema에 예제·기본값을 넣지 않는다.
10. 공개 API 추가 외 Migration·Web UI·Desktop Rust·Recovery API·BFF는 수정하지 않는다.

## 5. TDD 단계

1. Domain RED: Windows Native Session·Refresh 발급과 Web 불변 계약.
2. Runtime RED: Body allowlist, Server 고정 Platform/Kind, Cookie 0, Safe 오류.
3. OpenAPI RED: Route·Schema·응답·보안 경계.
4. 최소 Domain 구현 후 focused GREEN.
5. 최소 Runtime·OpenAPI 구현 후 focused GREEN.
6. Identity·Runtime·OpenAPI 관련 회귀와 Secret scan.

## 6. 필수 검증

```powershell
uv run --project services/api python -m pytest services/api/tests/test_identity_local_auth.py services/api/tests/test_identity_sessions.py services/api/tests/test_runtime_http.py -q
node --test scripts/tests/openapi-contract.test.mjs
node scripts/verify-openapi-contract.mjs
uv run --project services/api python -m pytest services/api/tests -q
git diff --check
```

전체 API 회귀의 기존 Skip은 그대로 보고하며 새 실패를 숨기지 않는다. 자동 테스트는 Windows 설치형 실제 로그인 PASS가 아니다.

## 7. 허용 변경 경로

- `services/api/src/daon_user_api/identity.py`
- `services/api/src/daon_user_api/runtime.py`
- `packages/contracts/openapi/v1/openapi.json`
- OpenAPI 생성 요약 파일이 Validator에 의해 정식 갱신되는 경우 해당 기존 요약 파일
- `services/api/tests/test_identity_local_auth.py`
- `services/api/tests/test_identity_sessions.py`
- `services/api/tests/test_runtime_http.py`
- `scripts/tests/openapi-contract.test.mjs`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-AUTH-C03_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-AUTH-C03_completion_report.md`

허용 경로 밖 변경이 필요하면 구현하지 말고 근거와 함께 어울1에게 되돌린다.

## 8. 보존 대상

- 사용자 삭제 표시 31건과 미추적 사용자 문서 3건
- 복원 완료된 두 Windows 파일의 HEAD Blob 내용
- 기존 Web 로그인·회원가입·메일 인증·비밀번호 재설정
- 기존 OIDC·Native Refresh·Step-up·Authorization·Recovery API
- 기존 DB·Backup·Restore·Object Storage 데이터

## 9. 진행 기록

착수, 정본 확인, RED, 최소 구현, 각 테스트, 오류·복구, 종료 직전에 다음 형식으로 즉시 기록한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

Commit·Push·배포·Browser·설치 실행은 수행하지 않는다.

## 10. 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

`status`는 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나다. 단일 명령·환경 오류는 원인과 대안을 조사하기 전 정식 실패로 보고하지 않는다.

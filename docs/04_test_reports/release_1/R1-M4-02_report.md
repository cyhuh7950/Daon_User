# R1-M4-02 Audit Event Core 작업보고서

## 판정

`COMPLETED` — 승인된 Core·OpenAPI·검증 범위를 구현했고 신규 회귀는 0건이다. HTTP/FastAPI, DB·Migration, Auth·RLS, Notification, App·UI·Workflow·Lockfile·외부 의존성은 변경하지 않았다.

## 판단 이유

- 불변 `AuditEvent`와 `AuditEventDraft`, Lock 안에서 Sequence·Previous Hash·Event Hash·중복 ID를 확정하는 append-only `AuditEventStore`를 구현했다.
- Actor·Tenant·Workspace·Target·Trace·Policy Version, 안전한 Before/After·Metadata를 검증하고 모든 의미 필드를 canonical JSON과 SHA-256 hash-chain에 결속했다.
- 중첩 민감 Key, 내부·Private 주소, 비 JSON 값, 비 UTC 시각, 누락·빈값·Enum 오류를 저장 전 fail-close한다.
- Public Store API는 append·read·list·verify_integrity만 노출하고, 반환 Projection은 깊은 불변 값이다.
- Tenant 필수 목록과 Workspace·Action·Outcome·Trace·Time·opaque Cursor Filter를 순수 Read로 제공한다.
- OpenAPI `/api/v1/audit-events`를 generic Resource 응답에서 전용 `AuditEventListResponse`로 교체하고 `AuditChange`·`AuditEvent`·Page/Envelope·Query 계약을 추가했다.
- 새 Python Source가 Quality Gate 신호를 활성화하므로 외부 도구를 추가하지 않고 `verify:api-audit`를 API lint/type/unit/build 필수 Capability 실행 계약에 연결했다.

## 생성·변경 결과

- Core: `services/api/src/daon_user_api/audit.py`, 공개 Package Export
- Test: `services/api/tests/test_audit_core.py`, `test_audit_integrity.py`
- 계약: `packages/contracts/openapi/v1/openapi.json`, OpenAPI Verifier/Test
- 실행: Root `verify:api-audit`, API Quality Gate Capability
- 문서: Architecture 계약, API README, Work Order·Prompt·Progress·본 보고서
- 증거: R1-M4-02 Audit Summary와 갱신된 R1-M4-01 OpenAPI Summary

## 테스트 결과

| 검증 | 결과 |
| --- | --- |
| Audit Unit·Integration·Tamper·Concurrency·Package Export | 11/11 PASS |
| `verify:api-audit -- --write` 후 no-write | PASS, Contract SHA `F859FE6645E312AB6E33F8C621EE54EFB262C480FA3F584469BAC83D812DE041` |
| OpenAPI Test | 8/8 PASS |
| OpenAPI write/no-write | PASS, 36 Paths·59 Operations·21 Schemas, SHA `AA1062932894A56D04B7CA3BE1923CBAB70E39385E086782544755A7FF0AE22C` |
| Workspace·Toolchain·Independence | 34/34 PASS·PASS·133 Files/0 Violations |
| JSON Parse·Node Syntax·Python Compile·Package Import | PASS |
| 전체 Node | 341개 중 331 PASS·iOS 10 FAIL; exact base `cc941e9`에서도 동일 파일 52/62로 신규 회귀 0 |
| Quality Gate | API Capability 보완 후 37/37 PASS 1회. 이후 최종 반복은 병렬 부하의 기존 Desktop Rust timing 1건으로 36/37; 같은 Rust 검증은 current와 exact base 각각 17/17 PASS |

Quality Gate의 간헐 실패는 `production_manager_error_fixtures_are_bounded_and_leave_no_processes`의 `state did not become ready`이며 본 작업은 Desktop 파일을 수정하지 않았다. 단독 current/base 통과와 통합 Gate 전체 통과가 모두 있어 R1-M4-02 신규 회귀로 판정하지 않는다. 같은 장시간 명령의 추가 무근거 반복은 중단했다.

## 조치

- 어울1은 Commit SHA와 원격 일치를 확인한 뒤 PR·CI·Merge를 수행한다.
- 후속 M4 작업은 이 Core와 OpenAPI 계약을 소비하되 실제 지속 저장·보존은 M5, 권한 강제는 M4-04/M4-05 소유 경계를 유지한다.
- 기존 iOS 10건과 Windows Desktop timing 변동은 R1-M4-02 범위 밖 기존 결함/환경 변동으로 별도 추적한다.

## 미해결 사항

- R1-M4-02 승인 범위의 미해결 사항 없음.
- 실제 HTTP·DB·권한·보존 실행은 후속 Work Order 범위이며 완료로 주장하지 않는다.

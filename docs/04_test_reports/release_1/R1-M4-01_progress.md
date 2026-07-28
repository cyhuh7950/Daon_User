# R1-M4-01 진행 복구 기록

| recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-29T02:05:00+09:00 | DEV-S0 | IN_PROGRESS | AGENTS·승인 설계 v0.7·계획 v0.9·테스트계획 v0.7·보안 시나리오·Monorepo 소유 경계·Contracts Package를 EOF까지 읽고 실제 §17.1 Path와 R1-M4-01 경계를 대조 | Work Order·Prompt·Progress | HEAD `400ff07b83452e7c8267ff00bbbb5118d94502b3`; branch `codex/r1-m4-01`; 착수 전 Clean; 단일 Writer·승인 명확 | 기존 OpenAPI/검증기/증거 없음 | TDD RED 계약 작성 | 기준 HEAD, 외부 작업0 |
| 2026-07-29T02:12:00+09:00 | DEV-RED | COMPLETED | 전체 Path·공통 안전 계약과 누락 Path·중복 operationId·unsafe error/absolute URL·Write Header·SSE 누락 거부 Test를 선고정 | `scripts/tests/openapi-contract.test.mjs`; Progress | 검증기 Module·OpenAPI 정본 부재에서 Target Test 예상 RED | 기능 막힘 없음 | OpenAPI 정본·무의존 검증기 구현 | Runtime/App/Service 변경0 |
| 2026-07-29T02:42:00+09:00 | DEV-GREEN | COMPLETED | OpenAPI 3.1 공통 계약·Package export·fail-close 검증기·결정적 요약 증거·소유 경계 문서를 구현 | OpenAPI·Contracts Package/README·Architecture·Verifier·Evidence·root script·Test·Progress | Target `6/6 PASS`; write/no-write 검증 `paths=36 operations=59 schemas=17 errors=6`; SHA `F10EAA9D...0886C`; Package self-export `1.0.0/36` | 최초 `--write`는 Sandbox mkdir EPERM, 승인된 격리 Worktree 권한 실행으로 정상 생성; 루트 Package import는 설치 Link 부재로 실패해 Package self-reference로 공개 Export 자체를 검증 | 전체 회귀·경계 검증 | Runtime/App/Service 변경0 |
| 2026-07-29T03:18:00+09:00 | DEV-VERIFY | IN_PROGRESS | 장시간 전역 회귀를 기준선 문제와 작업 변경 문제로 분리해 추적 | Progress만 추가; 제품 Source 추가 변경0 | OpenAPI 6/6, no-write 불변, JSON Parse, Package export, Independence 0건, Toolchain, Workspace 34/34, Desktop Lint/Build, iOS 제외 Node 277/277 PASS. `verify:desktop-rust-unit`은 기존 Local Service 준비 대기 1/14 실패, `verify:mobile`은 iOS 기준선 계약 10건 실패, `verify:quality-gate`는 304초 환경 Timeout | 격리 Worktree 의존성 누락은 `npm ci`로 복구. 전역 실패 대상 Source는 이 작업에서 변경 0건이며 동일 실행을 무근거 반복하지 않음 | 대상 Diff·보고서·최종 검증·Commit/Push | 현재 실행 중 명령 없음; 장시간 Quality Gate는 Timeout으로 중단됐고 대상 검증은 계속 진행 |
| 2026-07-29T03:43:00+09:00 | DEV-BASELINE-COMPARE | COMPLETED | exact base `400ff07b...` detached Worktree에서 iOS·Desktop Rust 동일 명령을 충분한 Timeout으로 비교 | Progress만 추가; `C:\tmp\Daon_User-r1-m4-01-baseline`은 비교용, 제품 Diff 0 | 기준선 iOS도 동일 10건 Exit 1; Desktop Build 후 Rust도 동일 Test `state did not become ready`, 13/14, Exit 1 | 현재 Diff는 실패 대상 `apps/mobile/ios/**`, `scripts/tests/ios-native-shell.test.mjs`, `apps/desktop/**`를 변경하지 않음. 따라서 두 실패는 `BASELINE_LIMITATION`, 이번 Diff 관련 회귀 0건 | 20분 Quality Gate 최종 Exit 확보 | 기준선 HEAD exact 확인 |
| 2026-07-29T03:49:00+09:00 | DEV-QUALITY-GATE | COMPLETED_WITH_BASELINE_LIMITATION | 변경 Worktree Quality Gate를 20분 제한으로 순차 재실행해 최종 판정 확보 | Progress만 추가; Gate 생성 R1-M1-05 증거와 `.coverage`는 작업 범위 밖이라 확인 후 원상 복구 | 272.1초, Exit 1; lint 7 PASS, type 4 PASS, unit 8 중 `desktop-shell-unit` 1 FAIL, contract 3 PASS, build 7 PASS, security 3 PASS, independence 1 PASS | 실패는 exact base에서도 재현된 Desktop Local Service ready 대기이며 OpenAPI Diff와 무관 | 보고서·최종 대상 검증·Commit/Push | Gate 실행 자체 Timeout 없음 |

## BASELINE_LIMITATION 상세

### Mobile iOS — 명령 Exit 1, 기준선과 변경 Worktree 동일 10건

공통 오류는 `AssertionError [ERR_ASSERTION]`, `assert.ok(contract|permissionContract|helper)`, `actual: ''`, `expected: true`다.

1. `Permission Phase ERR 진단은 함수 내부 실패 서비스만 allowlist 표식으로 추가하고 원 Exit를 보존한다`
2. `Permission XCTest 실패는 Raw Log를 보존하고 allowlist Code·Phase·원 Exit만 단일 Annotation한다`
3. `Permission XCTest 최종 Settings 진단은 검증된 마지막 한 줄만 Notice로 공개한다`
4. `Permission 실패는 마지막 알림 설정 Open Marker만 안전 검증해 Notice로 공개한다`
5. `Permission XCTest 실패는 Assertion Code 우선·마지막 허용 Stage 차선·Unknown 최종으로 분류한다`
6. `Permission Phase는 환경 상속 없이 세 고정 XCTest Method를 exact 매핑한다`
7. `C44 Search assertion과 Stage는 Bash allowlist에서 원 Exit 65로 분류된다`
8. `C46 Search input Summary Notice는 strict schema만 공개하고 원 Exit 65를 보존한다`
9. `C49 Surface Summary Notice는 strict schema·Bash3.2·원 Exit 65를 보존한다`
10. `C51 Result Summary Notice는 strict schema·Bash3.2·원 Exit65를 보존한다`

### Desktop Rust — 명령 Exit 1, 기준선과 변경 Worktree 동일 1건

- Test: `local_service::manager_tests::production_manager_error_fixtures_are_bounded_and_leave_no_processes`
- 결과: 14건 중 13 PASS, 1 FAIL.
- 오류: `src\local_service.rs:1094:13: state did not become ready`.
- 인과 확인: 이번 작업은 `apps/desktop/**`, Local Service와 해당 Test를 변경하지 않았고 exact base에서도 동일 실패했다.

| recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-29T03:56:00+09:00 | DEV-END | COMPLETED | 결과보고·허용 경계·최종 Target·no-write·Package export·Syntax·Diff를 재확인 | 허용 산출물 12개만 변경 | Target 6/6, OpenAPI no-write, Independence 0, root Package export 1.0.0/36, Node Syntax, `git diff --check` PASS | Quality/Mobile/Desktop 실패는 exact base 동일 `BASELINE_LIMITATION`; 관련 제품 Source 수정0 | 단일 목적 Commit·Push·원격 일치·Clean 확인 | Commit 직전 HEAD `400ff07b...` |

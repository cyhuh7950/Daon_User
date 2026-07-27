# R1-M3-06-C32 수정 작업지시서 — Quality Gate 안전 진단 Annotation

## 1. 판정

| 항목 | 값 |
| --- | --- |
| issue_id | `R1-M3-06-I008` |
| Attempt | `33` |
| 사유 | exact-SHA Quality Gate가 반복 실패하지만 공개 Annotation은 `exit code 1`만 제공하여 실패 Check를 판정할 수 없음 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-33.md` |

## 2. 확인된 증거

- exact Head `992d4679dbc2369d5df5db9356a74eede597ecd4`의 Quality Run `30269316603`은 준비·Toolchain·Lockfile·Desktop Rust 진단을 모두 통과했다.
- `Run common quality gate`만 Exit 1로 실패했고 Fallback Evidence 생성과 Artifact Upload는 성공했다.
- 공개 Check Annotation은 `.github:33`의 `Process completed with exit code 1.`뿐이다.
- GitHub 인증 제한으로 Artifact 원문을 안정적으로 수집할 수 없으므로, 실패 원인을 추측하거나 정책을 완화해서는 안 된다.

## 3. 설계 판단과 필수 작업

1. 기존 `quality-gate-result.json`의 현재 SHA 유효성 검증과 Fallback Evidence 계약을 그대로 유지한다.
2. Fallback 실행 뒤 현재 SHA의 Result JSON만 읽어 CI 로그와 Job Summary에 안전한 진단 한 줄을 출력하는 명시적 CLI 모드를 추가한다.
3. 출력 필드는 Allowlist로 제한한다: 고정 `CODE`, `overall_status`, 정수 `exit_code`, 실패 항목의 `category`, `code`, `check_id`, `component`만 허용한다.
4. `check_id`·`component`·`code`·`category`는 기존 안전 식별자 형식에 맞지 않으면 `UNAVAILABLE`로 치환한다. `evidence`, stdout/stderr, 명령, 경로, 환경값, 비밀값은 출력하지 않는다.
5. Result가 없거나 JSON이 손상됐거나 현재 `GITHUB_SHA`와 다르거나 최소 계약을 충족하지 않으면 고정 코드 `QUALITY_GATE_NO_CURRENT_RESULT`만 출력한다.
6. 실패 항목은 결정적 순서로 출력한다. 다수 실패를 지원하되 중복 제거와 합리적 상한을 두고, Raw 값 연결로 로그 주입이 불가능해야 한다.
7. Workflow의 `Ensure current-run quality gate evidence` 단계에서 Fallback 생성 후 안전 진단 CLI를 실행하고 결과를 `$GITHUB_STEP_SUMMARY`에도 기록한다. 진단 자체가 원 Quality Gate 판정을 덮거나 성공으로 바꾸면 안 된다.
8. Quality 정책·필수 Check·Exit Code·Toolchain·Product·iOS/Android 코드는 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 현재 SHA PASS/FAIL/ERROR, Stale/손상/누락 Result, 악성 식별자·비밀값·개행 주입, 다중 실패·중복·상한 계약 RED
- 구현 후 안전 진단 출력의 정확성·결정성·Fail-close PASS
- Workflow JSON 계약에서 Fallback 다음 진단, Summary 기록, Artifact Upload 순서를 검증
- `npm run verify:quality-gate` 자체의 정책·Exit 의미가 변경되지 않았음을 회귀 검증
- Quality 관련 Test, 전체 Node, Toolchain, Workflow JSON/Bash, Mobile 관련 회귀와 `git diff --check` PASS
- 허용 변경은 Quality Gate Library/CLI/Workflow/관련 Test, Progress와 Attempt 33뿐이다.
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·배포 금지.


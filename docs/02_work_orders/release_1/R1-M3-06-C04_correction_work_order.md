# R1-M3-06-C04 수정 작업지시서 — uv 버전 출력 Metadata 허용·Pin 엄격 비교

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `5` |
| 사유 | macOS uv `0.11.2`가 승인 Pin 뒤에 Build Metadata를 출력해 전체 문자열 완전 일치 비교가 오판 |
| 실패보고 | 0회 · 승인 버전 설치 성공 후 출력 형식 차이이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-5.md` |

## 2. 확인된 증거

- Run `30230343073`, Job `89867901401`에서 `setup-uv`는 성공했고 Action Log는 uv `0.11.2` 설치 성공을 기록했다.
- Fail-close Artifact `8639868484`의 실제 출력은 `uv 0.11.2 (02036a8ba 2026-03-26 aarch64-apple-darwin)`이다.
- Workflow의 `test "$(uv --version)" = "uv ${UV_PIN}"`은 승인 버전이 맞아도 뒤의 Build Metadata 때문에 실패한다.
- 기존 `scripts/verify-toolchain-baseline.mjs`는 `uv --version`의 두 번째 토큰을 승인 Pin과 비교한다.

## 3. 필수 수정·완료 조건

1. Workflow도 기존 Toolchain 검증과 동일하게 `uv --version`의 버전 토큰만 추출해 승인 Pin `0.11.2`와 엄격 비교한다.
2. 원문 전체 uv 출력은 Evidence Manifest에 계속 보존한다.
3. 승인 Pin 비교·`npm run verify:toolchain`·setup_uv Outcome·Fail-close 조건은 유지한다.
4. TDD로 Metadata가 붙은 실제 macOS 출력은 승인하고 다른 버전은 거부하는 계약을 고정한다.
5. iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android 회귀, `git diff --check` PASS.
6. Toolchain Pin·정책·기능 Source·Android Native·Signing 변경 금지.
7. Progress·Attempt 5에 Run/Job/Artifact와 RED/GREEN을 기록한다.
8. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속이다.

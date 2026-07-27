# R1-M3-06-C03 수정 작업지시서 — macOS CI 승인 uv 준비·증거 결속

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `4` |
| 사유 | exact-SHA macOS Run `30229512690`에서 승인 Toolchain 검증 전 `uv` 실행 파일 부재로 조기 종료 |
| 실패보고 | 0회 · GitHub-hosted Runner 준비 단계 누락이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-4.md` |

원 작업지시·승인 설계·계획·테스트 계획과 C01·C02는 계속 정본이다. 이 수정은 기능·공개 API·보안·Signing 범위를 변경하지 않고 승인 Toolchain을 macOS Runner에 재현하는 CI 보정이다.

## 2. 확인된 증거

- PR `#20`, Head SHA `fde87e48eaa4ef213f0fbf94e6811942b039052d`의 macOS Run `30229512690`, Job `89865606238`.
- Checkout·Node·Xcode Step은 성공했고 `macos26`, Xcode `26.6`·Build `17F113`가 기록됐다.
- `Verify exact Node and npm`에서 `npm run verify:toolchain`이 `spawnSync uv ENOENT`로 실패했다.
- Fail-close Artifact는 `FAILED`, `verification_completed:false`, `failed_steps:[node_npm]`, 후속 Step skipped, Simulator·4개 XCTest Result missing을 정확히 기록했다.

## 3. 필수 수정

1. macOS Workflow에 안정 ID `setup-uv`를 가진 승인 uv 설치 Step을 Toolchain 검증 전에 추가한다.
2. 버전은 `toolchain-versions.json`의 승인 Pin `0.11.2`와 자동 대조되게 하며 임의 최신 버전을 사용하지 않는다.
3. 기존 공통 Workflow에서 사용 중인 `astral-sh/setup-uv@v7` 패턴을 재사용할 수 있다.
4. `uv --version`과 `npm run verify:toolchain`으로 실제 설치·Pin을 검증한다.
5. `setup_uv`를 Workflow Outcome·`write-evidence.mjs` 필수 Step에 추가해 실패·누락·Skip을 성공으로 기록하지 못하게 한다.
6. Evidence Manifest Toolchain에 실제 uv 버전을 포함하고 `unknown`이면 성공을 금지한다.
7. TDD로 다음을 RED→GREEN 고정한다.
   - 승인 uv 설치 Step과 정확 Pin
   - Toolchain 검증보다 앞선 순서
   - Outcome·필수 Step·Manifest uv 버전 결속
   - 실패/Skip Fixture의 Fail-close 유지

## 4. 금지·완료 조건

- Toolchain Pin·Quality Gate 정책·보안 규칙 약화, Step Skip·성공 강제, 최신 버전 임의 설치 금지
- iOS 기능 Source·Android Native Production·Signing Asset 변경 금지
- iOS Root Gate, Evidence Fixture, Mobile·Android 회귀, Workflow Parse, Node/Bash Syntax와 `git diff --check` PASS
- Progress·Attempt 4에 Run/Job/Artifact 증거와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속

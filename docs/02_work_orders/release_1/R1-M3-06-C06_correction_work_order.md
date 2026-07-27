# R1-M3-06-C06 수정 작업지시서 — 승인 CocoaPods 절대 실행경로 결속

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `7` |
| 사유 | 격리 Gem 설치 성공 후에도 macOS Runner의 일반 `pod` 해석이 선설치 `1.17.0`을 유지 |
| 실패보고 | 0회 · PATH 선택 환경 차이이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-7.md` |

## 2. 확인된 증거

- Run `30231718788`, Job `89871737982`에서 승인 CocoaPods `1.16.2`와 45개 Gem은 RUNNER_TEMP 격리 경로에 설치 성공했다.
- CocoaPods Step은 설치 직후 실패했고 Artifact `8640306747`은 `failed_steps:[cocoapods]`를 기록했다.
- 같은 Artifact의 Manifest가 후속 일반 `pod --version`을 `1.17.0`으로 기록해 PATH 기반 선택이 Runner에서 유지되지 않았음을 확인했다.

## 3. 필수 수정

1. 설치된 `${POD_GEM_BIN}/pod`가 실행 가능하고 버전 `1.16.2`인지 절대경로로 직접 검증한다.
2. 그 절대경로를 `DAON_POD_BIN`으로 `$GITHUB_ENV`에 기록한다.
3. 후속 Pods Step은 일반 `pod`가 아니라 `${DAON_POD_BIN}` 절대경로로 두 번의 install과 버전 확인을 수행한다.
4. Manifest도 `${DAON_POD_BIN}` 절대경로에서 실제 CocoaPods 버전을 수집한다. 변수 누락 시 Fail-close용 빈/unknown 증거를 만들되 Runner 일반 `pod`로 조용히 Fallback하지 않는다.
5. `GEM_HOME`·`GEM_PATH`는 격리 Gem Runtime 의존성 검색을 위해 유지한다.
6. Runner 선설치 CocoaPods 삭제·덮어쓰기와 Repository Gem 설치는 금지한다.
7. 현재·후속 Pods·Manifest가 동일 승인 절대경로를 호출하는 TDD 계약을 추가한다.

## 4. 완료 조건

- iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- 일반 `pod` 실행으로 승인 여부를 판정하는 경로 0건(진단 목적 원문도 승인 절대경로 사용)
- Repository Gem/Temp/Signing 잔존 0, 기능 Source·Android Native·정책 변경 0
- Progress·Attempt 7에 Run/Job/Artifact와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속

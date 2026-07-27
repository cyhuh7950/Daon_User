# R1-M3-06-C07 수정 작업지시서 — CocoaPods Gem 본체 실행 Script 직접 호출

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `8` |
| 사유 | 승인 Gem 설치 성공 직후 `${POD_GEM_BIN}/pod` Wrapper 실행 가능 검사에서 무출력 종료 |
| 실패보고 | 0회 · RubyGems Wrapper 생성 위치/형식 환경 차이이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-8.md` |

## 2. 확인된 증거

- Run `30232408873`, Job `89873623882`에서 승인 CocoaPods `1.16.2`와 45개 Gem 설치가 성공했다.
- 설치 완료 출력 직후 첫 무출력 검사인 `test -x "${POD_GEM_BIN}/pod"` 구간에서 Step이 종료됐다.
- Artifact는 CocoaPods Step 실패·후속 skipped를 Fail-close로 기록했다.
- Wrapper/Bin 위치는 Runner RubyGems 환경에 따라 신뢰할 수 없지만, 설치 Repository의 `gems/cocoapods-1.16.2/bin/pod`는 Gem 본체 실행 Script의 결정 경로다.

## 3. 필수 수정

1. 승인 Gem 설치 후 `${POD_GEM_HOME}/gems/cocoapods-1.16.2/bin/pod` 파일 존재를 확인한다.
2. 이를 `DAON_POD_SCRIPT`로 `$GITHUB_ENV`에 기록한다.
3. 현재 Step·후속 Pods 두 install·Manifest는 모두 `ruby "${DAON_POD_SCRIPT}" ...`로 동일 Script를 직접 실행한다.
4. 현재 Step에서 실제 버전을 변수로 받아 로그에 출력한 뒤 `1.16.2`와 엄격 비교한다.
5. `DAON_POD_SCRIPT` 누락·파일 부재 시 일반 `pod`나 Wrapper로 Fallback하지 않고 Fail-close한다.
6. `GEM_HOME`·`GEM_PATH`는 격리 Gem 의존성 검색을 위해 유지한다. PATH/GITHUB_PATH Wrapper 우선순위는 더 이상 완료 근거로 사용하지 않는다.
7. 현재·후속 Pods·Manifest 동일 Script 결속과 다른 버전 거부를 TDD로 고정한다.

## 4. 완료 조건

- iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- Workflow 실행 경로에 일반 `pod` 또는 `${POD_GEM_BIN}/pod` Wrapper 의존 0건
- Repository Gem/Temp/Signing 잔존 0, 기능 Source·Android Native·정책 변경 0
- Progress·Attempt 8에 Run/Job/Artifact와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속

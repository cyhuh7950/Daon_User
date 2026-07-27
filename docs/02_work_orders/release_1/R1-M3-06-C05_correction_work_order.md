# R1-M3-06-C05 수정 작업지시서 — CocoaPods 승인 버전 격리 실행

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `6` |
| 사유 | macOS Runner 선설치 `pod 1.17.0`이 새로 설치한 승인 `1.16.2`보다 PATH에서 우선되어 버전 검증 오판 |
| 실패보고 | 0회 · 승인 Gem 설치 성공 후 실행 파일 선택 충돌이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-6.md` |

## 2. 확인된 증거

- Run `30231012154`, Job `89869787656`에서 Node/npm/uv Toolchain 검증까지 성공했다.
- `gem install cocoapods -v 1.16.2 --no-document`는 CocoaPods `1.16.2` 설치 성공을 기록했다.
- 직후 `test "$(pod --version)" = "1.16.2"`가 실패했다. 직전 Artifact에서 Runner 선설치 `pod --version`은 `1.17.0`이었다.
- Fail-close Artifact `8640093821`이 업로드됐고 후속 Build·Simulator·XCTest는 실행되지 않았다.

## 3. 필수 수정

1. 승인 CocoaPods `1.16.2`와 그 의존성을 `${RUNNER_TEMP}` 아래 전용 Gem Repository/Bin에 설치한다.
2. RubyGems의 `--install-dir`·`--bindir`를 사용하고, 전용 Bin을 현재 Step PATH와 `$GITHUB_PATH`의 최우선으로 고정한다.
3. 전용 Gem Repository와 기존 Ruby 기본 Gem 검색 경로를 안전하게 결합한 `GEM_HOME`·`GEM_PATH`를 현재 Step과 `$GITHUB_ENV`에 기록해 후속 `pod install`에서도 동일 `1.16.2`를 사용한다.
4. Runner 선설치 CocoaPods를 삭제·덮어쓰기하지 않는다.
5. 현재 Step과 별도 후속 Step 환경을 모사한 계약 Test에서 `pod --version = 1.16.2` 선택을 고정한다.
6. 기존 `cocoapods` Outcome·Manifest 실제 버전·Fail-close 조건을 유지한다.

## 4. 금지·완료 조건

- 승인 Pin·전역 Runner Gem 삭제·Quality/Security 정책·기능 Source·Android Native·Signing 변경 금지
- iOS Root Gate, Workflow Parse/Bash Syntax, Mobile·Android 회귀, 전체 Node, Toolchain, `git diff --check` PASS
- 격리 경로가 Repository나 Artifact에 Gem 설치물을 생성하지 않음
- Progress·Attempt 6에 Run/Job/Artifact와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속

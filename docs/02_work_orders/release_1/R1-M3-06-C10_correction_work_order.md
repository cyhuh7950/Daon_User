# R1-M3-06-C10 수정 작업지시서 — CocoaPods Gem 이름과 pod 실행 파일 명시

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `11` |
| 사유 | RubyGems가 실행 파일 `pod`를 동일 이름의 Gem으로 추정해 존재하지 않는 `pod (= 1.16.2)` Gem을 조회 |
| 실패보고 | 0회 · Gem 이름과 실행 파일 이름이 다른 경우의 명시 옵션 누락이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-11.md` |

## 2. 확인된 증거

- Run `30234549624`, Job `89879720903`, Artifact `8641203838`은 Head `3498a4776d1cab548e2abe00473d34a6c6c3a75c`의 실행 결과다.
- Node·npm·uv 검증과 `cocoapods` Gem `1.16.2` 및 45개 Gem 설치는 성공했다.
- `gem exec -v 1.16.2 pod --version`은 `Could not find a valid gem 'pod' (= 1.16.2) in any repository`로 종료했다.
- RubyGems 공식 `gem exec` 옵션 `-g, --gem GEM`은 실행 파일과 다른 실제 Gem 이름을 지정한다. CocoaPods Gem 이름은 `cocoapods`, 실행 파일은 `pod`다.
- Fail-close Artifact·Simulator 종료는 정상이고 기능·Build·XCTest 단계는 실행되지 않았다.

## 3. 필수 수정

1. 현재 버전 확인을 `gem exec -g cocoapods -v 1.16.2 pod --version`으로 교정한다.
2. 후속 Pods 두 install과 Manifest 버전 수집도 동일한 `-g cocoapods` 명시 형식을 사용한다.
3. `-g cocoapods`가 없는 `gem exec`, 잘못된 `-- pod`, 직접 Script·Wrapper·일반 `pod`로 Fallback하지 않는다.
4. 승인 버전 엄격 비교, 격리 `GEM_HOME`·`GEM_PATH`, Outcome·Evidence Fail-close 계약을 유지한다.
5. Gem 이름 `cocoapods`와 실행 파일 `pod`의 결속, 3회 버전 확인·2회 install, 누락 형식 거부를 TDD로 고정한다.
6. 기능 Source, Android Native, 공개 계약, Signing, Toolchain Pin과 Evidence 상태 의미는 변경하지 않는다.

## 4. 완료 조건

- iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- exact `gem exec -g cocoapods -v 1.16.2 pod` 버전 확인 3회·install 2회
- `-g cocoapods` 누락·`-- pod`·직접 Script·Wrapper·일반 pod Fallback 0건
- Repository Gem/Temp/Signing 잔존 0, 보호 범위 Diff 0
- Progress·Attempt 11에 Run/Job/Artifact와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속


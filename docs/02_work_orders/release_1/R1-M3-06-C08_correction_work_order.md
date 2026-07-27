# R1-M3-06-C08 수정 작업지시서 — RubyGems 버전 지정 실행으로 CocoaPods 호출

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `9` |
| 사유 | 승인 Gem 본체 Script를 `ruby`로 직접 실행하면 Bundler가 Gem 설치 디렉터리의 존재하지 않는 `Gemfile`을 요구하여 종료 |
| 실패보고 | 0회 · GitHub macOS RubyGems/Bundler 실행 문맥 문제이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-9.md` |

## 2. 확인된 증거

- Run `30233140401`, Job `89875651654`, Artifact `8640735155`는 PR Head `1000cee621fac4f92d7597357e1459201dce1d56`의 실행 결과다.
- Node·npm·uv 검증과 승인 CocoaPods `1.16.2` 및 45개 Gem 설치는 성공했다.
- `ruby "${DAON_POD_SCRIPT}" --version`에서 CocoaPods 실행 Script가 `bundler/setup`을 불러오며 `${POD_GEM_HOME}/gems/cocoapods-1.16.2/Gemfile not found (Bundler::GemfileNotFound)`로 종료했다.
- 후속 단계는 skipped였고 Fail-close Artifact는 CocoaPods 실패·`verification_completed:false`를 정확히 기록했다.
- RubyGems의 `gem exec`는 설치된 Gem 실행 명령을 버전으로 지정해 호출하는 공식 실행 경로이므로, Gem 본체 Script를 Ruby로 직접 호출하지 않는다.

## 3. 필수 수정

1. 승인 CocoaPods 설치와 격리 `GEM_HOME`·`GEM_PATH`는 유지한다.
2. 현재 버전 확인을 `gem exec -v 1.16.2 -- pod --version`으로 실행하고 실제 출력 로그 및 `1.16.2` 엄격 비교를 유지한다.
3. 후속 Pods 두 install과 Manifest 버전 수집도 동일하게 `gem exec -v 1.16.2 -- pod ...`를 사용한다.
4. `DAON_POD_SCRIPT`, Gem 본체 `ruby` 직접 실행, 일반 `pod`, Wrapper 절대경로로 Fallback하지 않는다.
5. `gem exec` 실패 또는 버전 불일치는 기존 Outcome·Evidence 계약에 따라 Fail-close한다.
6. 현재·후속 Pods·Manifest의 동일 `gem exec` 결속, 승인 버전 선택, 다른 버전 거부, 직접 Script/Wrapper/일반 pod Fallback 0건을 TDD로 고정한다.
7. 기능 Source, Android Native, 공개 계약, Signing, 승인 Toolchain Pin과 Evidence 상태 의미는 변경하지 않는다.

## 4. 완료 조건

- iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- Workflow 실행 경로의 CocoaPods 명령이 모두 exact `gem exec -v 1.16.2 -- pod` 계약을 사용
- Repository Gem/Temp/Signing 잔존 0, 기능 Source·Android Native·정책 변경 0
- Progress·Attempt 9에 Run/Job/Artifact와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속


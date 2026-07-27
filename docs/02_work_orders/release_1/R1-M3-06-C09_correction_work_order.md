# R1-M3-06-C09 수정 작업지시서 — RubyGems exec 명령 인자 경계 교정

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I001` |
| Attempt | `10` |
| 사유 | `gem exec -v 1.16.2 -- pod ...`에서 `--` 뒤의 `pod`가 실행 파일로 해석되지 않아 `Please specify an executable to run` 종료 |
| 실패보고 | 0회 · 공식 명령 형식의 인자 경계 오해이며 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-10.md` |

## 2. 확인된 증거

- Run `30234129587`, Job `89878471765`, Artifact `8641046506`는 Head `30c1c76caaeba2154ffd20874bdbd25f6ee164c1`의 실행 결과다.
- Node·npm·uv 검증과 승인 CocoaPods `1.16.2` 및 45개 Gem 설치는 성공했다.
- 첫 `gem exec -v 1.16.2 -- pod --version`에서 RubyGems `exec_command.rb#check_executable`이 `Please specify an executable to run (Gem::CommandLineError)`를 반환했다.
- RubyGems 공식 예시는 `gem exec rails new .`처럼 실행 파일을 명령 바로 뒤에 두며, `gem exec` Usage의 `COMMAND`가 실행 파일이다. 이 Runner에서는 별도 `--`가 COMMAND를 전달하지 못하므로 사용하지 않는다.
- Fail-close Artifact와 Simulator 종료는 정상이며 기능·Build·XCTest 단계는 실행되지 않았다.

## 3. 필수 수정

1. 현재 버전 확인을 `gem exec -v 1.16.2 pod --version`으로 교정한다.
2. 후속 Pods 두 install과 Manifest 버전 수집도 같은 형식 `gem exec -v 1.16.2 pod ...`을 사용한다.
3. `gem exec -v 1.16.2 -- pod`, 직접 Script, Wrapper, 일반 `pod`로 Fallback하지 않는다.
4. 승인 `1.16.2` 엄격 비교, `GEM_HOME`·`GEM_PATH`, Outcome·Evidence Fail-close 계약은 유지한다.
5. 현재·후속 Pods·Manifest 명령 형식과 잘못된 `-- pod` 형식 거부를 TDD로 고정한다.
6. 기능 Source, Android Native, 공개 계약, Signing, Toolchain Pin과 Evidence 상태 의미는 변경하지 않는다.

## 4. 완료 조건

- iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- CocoaPods 실행은 exact `gem exec -v 1.16.2 pod` 버전 확인 3회·install 2회
- 잘못된 `gem exec ... -- pod`, 직접 Script·Wrapper·일반 pod Fallback 0건
- Repository Gem/Temp/Signing 잔존 0, 보호 범위 Diff 0
- Progress·Attempt 10에 Run/Job/Artifact와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속


# R1-M3-06-C11 수정 작업지시서 — Monorepo iOS Autolinking 기준 Root 고정

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I002` |
| Attempt | `12` |
| 사유 | 실제 Pod install에서 React Native CLI Autolinking이 Mobile App Root가 아닌 호출 작업 디렉터리를 기준으로 해 iOS Project 설정을 얻지 못함 |
| 실패보고 | 0회 · 처음 도달한 Native Podfile 구성 결함이며 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-12.md` |

## 2. 확인된 증거

- Run `30235018616`, Job `89881083699`, Artifact `8641360743`은 Head `605bbb180518502b18803011faa61bdc497fcc63`의 실행 결과다.
- 승인 CocoaPods `1.16.2`, npm lockfile 설치, Portable iOS·Mobile 회귀가 모두 성공해 CocoaPods 실행 경로 문제는 해소됐다.
- 첫 `pod install --project-directory=apps/mobile/ios`에서 Podfile 12행 `config = use_native_modules!` 내부가 `undefined method '[]' for nil`로 종료했다.
- `npx react-native config`를 `apps/mobile`에서 실행하면 `root=apps/mobile`, `project.ios.sourceDir=apps/mobile/ios`, `reactNativePath=<repo>/node_modules/react-native`를 정상 반환한다.
- 저장소 Root에는 Mobile 전용 `react-native.config.js`가 없고 기본 Autolinking 명령은 호출 CWD에 의존하므로 Monorepo App Root를 명시적으로 고정해야 한다.

## 3. 필수 수정

1. Podfile에서 Mobile App Root를 `__dir__` 기준으로 결정하고, Autolinking CLI가 그 Root에서 실행되도록 `use_native_modules!`에 명시적 명령을 전달한다.
2. 경로는 개인 절대경로가 아니라 Podfile 위치에서 계산하고, Node Module은 기존 Hoist Resolver와 승인 Lockfile을 사용한다.
3. 명시적 Autolinking 명령은 `apps/mobile` 기준 CLI config가 `project.ios`, `reactNativePath`, 빈 native dependency 목록까지 유효하게 반환함을 검증해야 한다.
4. `config[:reactNativePath]`와 기존 `use_react_native!`·`react_native_post_install` 계약은 유지하고 임의의 하드코딩 결과 또는 Autolinking 우회·삭제를 금지한다.
5. Repository Root와 Mobile Root가 다른 Monorepo에서도 CWD와 무관하게 같은 App Root를 선택하는 계약을 TDD로 고정한다.
6. 기능 Source, Android Native, 공개 계약, Signing, CocoaPods/Toolchain Pin과 Evidence 상태 의미는 변경하지 않는다.

## 4. 완료 조건

- Podfile/Autolinking 계약 RED→GREEN 및 `npx react-native config`의 Mobile iOS Project 유효성 확인
- iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- 개인 절대경로·Autolinking 우회·Repo Root config 추가·Signing 변경 0건
- Repository Pods/Build/Gem/Temp 잔존 0, 보호 범위 Diff 0
- Progress·Attempt 12에 Run/Job/Artifact와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속


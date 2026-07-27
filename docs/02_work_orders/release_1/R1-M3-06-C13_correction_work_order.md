# R1-M3-06-C13 수정 작업지시서 — LaunchScreen Storyboard의 Xcode 문서 계약 복구

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I004` |
| Attempt | `14` |
| 사유 | Xcode 26.6 `ibtool`이 `LaunchScreen.storyboard`의 문서 도구 버전 메타데이터를 해석하지 못해 unsigned Simulator Build가 종료됨 |
| 실패보고 | 0회 · 처음 도달한 Interface Builder 문서 계약 결함이며 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-14.md` |

## 2. 확인된 증거

- Run `30236247138`, Job `89884498651`, Head `36ca17837e88d5bb99eae0fcf90d1ae0b973fa63`의 exact-SHA 실행이다.
- Checkout, Node/npm/uv, CocoaPods `1.16.2`, Portable 회귀, Pods 재현 설치와 exact Simulator 생성은 모두 성공했다.
- C12에서 추가한 React Bridging Header 뒤 `DaonIOSHost.swift`와 `AppDelegate.swift`의 arm64·x86_64 Swift Compile은 통과했다.
- unsigned Release Build의 유일한 실패 명령은 `CompileStoryboard apps/mobile/ios/Daon/LaunchScreen.storyboard`다.
- `ibtoold`는 `IBDocumentUnarchiving-ToolsVersion`의 값이 nil이라는 예외를 남겼고, `ibtool`은 문서를 열 수 없다는 `com.apple.InterfaceBuilder error -1`과 Exit 65를 반환했다.
- 현재 Storyboard의 `<document>`에는 `toolsVersion`·`systemVersion`·`sourceToolsVersion` 등 Xcode가 직렬화하는 문서 호환성 메타데이터가 없다.
- Fail-close Outcome·Manifest 작성, Simulator shutdown/delete와 Artifact Upload는 성공했다. 후속 UI/Simulator 검증은 의도대로 skipped 처리됐다.

## 3. 필수 수정

1. 승인 React Native Template Commit과 현재 Xcode 26.6에서 인식 가능한 Launch Screen 문서 형식을 대조해 현재 Storyboard를 최소 교정한다.
2. 임의 도구 버전을 추측해 넣지 말고, 승인 Template 또는 Xcode가 생성한 정식 형식에서 근거를 확보한다. 필요한 `document` 메타데이터와 `dependencies`/capability 선언만 반영한다.
3. 기존 표시명 `Daon`, 중앙 정렬, 16pt 제목, 배경색과 Auto Layout 의미를 보존한다.
4. Bundle ID, Deployment Target, Signing, 권한, Deep Link, Swift Host, Pods, App/UI Test Target과 JS 기능 Source는 변경하지 않는다.
5. XML well-formed, 필수 Interface Builder 문서 메타데이터, initial View Controller와 모든 참조 ID 무결성을 TDD 계약으로 고정한다.
6. Windows Portable 검증이 실제 macOS `ibtool` 성공을 대신한다고 선언하지 않는다. 완료 상태는 새 exact-SHA macOS Build·Simulator 실행 전까지 `IMPLEMENTED_PENDING_MACOS_CI`로 유지한다.

## 4. 완료 조건

- Storyboard 문서 계약 RED→GREEN 및 XML/참조 무결성 PASS
- iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- Storyboard 외 Production 변경은 계약 Test에 꼭 필요한 최소 범위로 제한
- Bundle ID·Signing·Pods·Target·Swift Host·기능 Source·Android Native 변경 0
- 개인 절대경로·Generated Pods/Build/Gem/Temp·Signing Asset 잔존 0
- Progress·Attempt 14에 Run/Job/Head, 실패 명령, `ibtool` 원문 증거와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속


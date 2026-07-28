COMPLETED | R1-M3-06-I007 | C56 fresh Notification switch 조회와 bounded 상태 증거 | Swift XCTest·Simulator Notice parser·정적/Runtime 계약·C56 문서·Progress·Attempt57 | RED0/1→iOS67/67·Mobile·Node331/331·Toolchain/YAML/Bash/Bundle/Diff PASS | 실제 macOS exact-SHA Runtime·최종 Artifact 미확인 | 단일 Commit·Push 후 어울1 CI 판정

# R1-M3-06 Attempt 57 결과보고

## 판정

- `COMPLETED`. 기준 HEAD `07fb987b1f718643c90fa008e35b7dc65d2c015b`.
- 동일 issue의 정식 `FAILURE_REPORT` 0회, C56 `INCOMPLETE` 0회, TP Wave 미도달.

## 판단 이유

Run `30365939432`의 `SETTINGS_SWITCH_VALUE_FAILED`는 tap 전에 획득한 `XCUIElement`를 Wait predicate와 최종 판정까지 재사용해 실제 Settings 상태와 분리될 수 있는 경로에서 발생했다. switch 후보는 매번 `ALLOW_NOTIFICATIONS_ID` exact query를 최우선으로 새 조회하고, 0건일 때만 기존 영문·한글 exact label query를 사용하도록 변경했다. before·tap target·after·polling·final은 모두 새 query를 수행하며 상태 변경 tap은 최대 1회다.

## 생성·변경 결과

- `DaonUITests.swift`: stable identifier 우선 후보, 단일성·hittable fail-close, NSNumber/String 정규화, before/after/final Marker와 fresh polling/final 판정.
- `verify-simulator.sh`: failure log의 switch Marker를 최대 3행·320자·고정 schema로 모두 검증한 뒤에만 Notice로 공개.
- `ios-native-shell.test.mjs`: fresh query·1회 tap·금지 fallback과 valid/injection/4행/잘못된 phase Parser Fixture 계약 추가, 구 slice 경계 정합.
- C56 작업지시서·프롬프트·Progress와 본 보고서 생성·갱신.

## 검증

- TDD RED: C56 0/1, 신규 stable query 부재를 예상대로 검출.
- 첫 전체 iOS: 59/67. 신규 기능은 PASS했고 구 helper 종료명·Parser 인접 배치를 가정한 정적 계약 8건만 실패해 범위를 새 helper 경계로 정합했다.
- Targeted 관련 9/9, iOS Native 67/67 PASS.
- Mobile: Lint 14, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 67/67, Bundle PASS.
- 전체 Node 331/331, Toolchain 7, Workflow YAML/JSON 2/2, iOS CI Bash 3/3, Node syntax, `git diff --check` PASS.
- Bundle identifier는 `com.sinsan.daon` 계약을 유지하고 hash도 동일: Android 927506 bytes / `D3289CE9B7AC710D833FEBD8DCB67E32B39D319810E1E853A63EB3547531E5AE`; iOS 921716 bytes / `BA97DD2195EDB6225460D9DFA70B8726040EB0D04C2D78429AC4068D0E8E6616`.
- 앱 제품 코드·공개 계약·Signing·Phase B·Android·타 플랫폼·dependency/lock/project 보호 변경 0건.

## 조치

- 허용 Diff를 단일 목적 Commit으로 branch `codex/r1-m3-06`에 Push하고 exact SHA를 어울1에게 인계한다.
- 실제 macOS Simulator에서 before/after/final Marker와 revoke·grant 결과, 원 Exit 65 보존 및 최종 Artifact는 어울1이 exact-SHA CI로 판정한다.

# R1-M3-06-C49 수정 작업지시서

## 문서 상태

- 상태: APPROVED
- 실행: Attempt 50
- 동일 문제: `R1-M3-06-I007`
- 기준 HEAD: `b1bb0576edf3a973b09572cb76a35d9e36d97937`

## 확인된 증거와 목적

- exact-SHA Run `30346370694` attempt 2, Job `90234247054`는 Portable 57/57과 Build/general UI를 통과했다.
- Permission revoke에서 exact Search button tap 뒤 `SETTINGS_SEARCH_FIELD`, 기존 Summary `v1|count=0|items=_none_`, `SETTINGS_SEARCH_FIELD_MISSING/revoke/65`가 확인됐다.
- selector를 바꾸지 않고 Search 탭 후 실제 접근성 표현과 화면 전환 여부를 bounded Surface Summary로 진단한다.

## 구현 계약

- Product/Swift selector·tap/input·Stage·Assertion·Exit 65와 기존 C46 Summary 계약을 변경하지 않는다.
- 별도 prefix `DAON_SETTINGS_SEARCH_SURFACE_SUMMARY=v1`을 사용하고 failure guard에서 기존 input Summary 다음 정확히 한 번 출력한다.
- 후보 우선순위는 `textView`, `other`, `button`, `staticText`; 총 최대 24개다.
- 각 후보는 non-empty label/identifier/value 또는 hittable인 요소만 포함한다. button은 exact identifier `com.apple.settings.search`를 우선하고 나머지는 deterministic 순서로 제한 수집한다.
- field는 existing sanitizer로 최대 48자, `isHittable`을 포함한다. debug/frame/pid/path/env/keyboard를 출력하지 않는다.
- Simulator Script는 strict-valid 단일 Surface Summary만 Notice로 공개한다. 허용 elementType은 `textView|other|button|staticText`, count 0/max24, token 48, 전체 4096과 delimiter/schema/injection 검증을 적용한다.
- C48 Bash 3.2 단일 문자열 방식만 사용하고 `mapfile`은 금지한다.
- RED→GREEN에서 valid/empty/multiple/invalid/injection/oversize/count mismatch와 원 Exit 65 보존을 검증한다.

## 변경 범위와 검증

- 허용: `apps/mobile/ios/DaonUITests/DaonUITests.swift`, `apps/mobile/ios/ci/verify-simulator.sh`, `scripts/tests/ios-native-shell.test.mjs`, C49 문서·Progress·Attempt 50.
- 금지: Product/Android/Workflow/의존성/Lock/Project/Signing과 Commit/Push/PR/GitHub/SSH/Server/GUI.
- Progress에 착수·RED·GREEN·전체 검증·종료를 기록한다.
- iOS Native, Mobile, 전체 Node, Toolchain, Workflow YAML, Bash, Bundle, Diff·보호 경계를 검증한다.

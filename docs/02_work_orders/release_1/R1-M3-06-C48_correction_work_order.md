# R1-M3-06-C48 수정 작업지시서

## 문서 상태

- 상태: APPROVED
- 실행: Attempt 49
- 동일 문제: `R1-M3-06-I007`
- 기준 HEAD: `5957efcae973b3f709a9bd9e5ac6f16c0bb22006`

## 확인된 원인과 목표

- exact-SHA macOS 실행에서 Portable iOS 55/56, Settings Search valid Summary의 Notice가 0건이었다.
- C46에서 추가한 `mapfile -t source_lines`는 macOS 기본 Bash 3.2에서 지원되지 않는다.
- `report_settings_search_accessibility_notice`의 `mapfile`과 `source_lines` 배열만 Bash 3.2 호환 단일 문자열 수집으로 교체한다.

## 구현 계약

- `source_line="$(grep -F "${prefix}" "${log_file}" || true)"`로 수집한다.
- 결과가 비어 있으면 Notice를 생성하지 않는다.
- 결과에 내부 개행 `$'\n'`이 있으면 중복 유효 행으로 보고 Notice를 생성하지 않는다.
- `${source_line#*${prefix}}` payload 추출 의미와 C47 token validator, 구조 ERE, count 0/max 16, 4096 byte, schema, Exit 65, Swift Summary 계약을 보존한다.
- TDD 정적 계약에 `mapfile`/`source_lines` 부재, command substitution, nonempty/newline rejection을 고정한다.
- 기존 valid/u{AC00}, absent, duplicate, invalid, injection, oversize Fixture와 명시적 유효 2행 거부를 보존한다.

## 변경 범위

- 허용: `apps/mobile/ios/ci/verify-simulator.sh`, `scripts/tests/ios-native-shell.test.mjs`, 본 C48 문서·프롬프트·Progress·Attempt 49 보고서.
- 금지: Swift/Product/Android/Workflow/의존성/Lock/Project/Signing, Commit/Push/PR/GitHub/SSH/Server/GUI.

## 진행 기록과 완료 검증

- `docs/04_test_reports/release_1/R1-M3-06_progress.md`에 착수, RED, GREEN, 전체 검증, 종료를 기록한다.
- iOS Native, Mobile, 전체 Node, Toolchain, Workflow YAML, Bash, Bundle, Diff와 보호 경계를 검증한다.
- 종료 보고는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식을 따른다.

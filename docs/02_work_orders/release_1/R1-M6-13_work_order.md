# R1-M6-13 작업지시서 — RuleSet Connector·Binding

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-13` |
| Issue ID | `R1-M6-13-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §7.4, §9.4, §12.5, §18.2 |
| 계획 근거 | Release 1 계획 R1-M6-13 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-13_progress.md` |

## 목적

RuleSet을 선택·강제 Binding하고 불변 Version Snapshot과 만료·폐기·장애 동작을 보존한다.

## 계약

- 선택 Binding은 RuleSet 미가용 시 해당 기능을 보존하고 경고한다.
- 강제 Binding은 유효 Snapshot이 없으면 `RULESET_UNAVAILABLE`로 fail-closed 차단한다.
- 평가 결과에는 RuleSet ID·Version·Binding mode·Audit 사유를 남긴다.
- 만료·폐기 RuleSet은 유효 Snapshot으로 사용하지 않는다.
- Connector 장애와 Rule 위반을 구분한다.

## 제외

실제 Daon RuleSet API·브라우저 UI·배포.

## 허용 변경 파일

- `services/api/src/daon_user_api/ruleset_connector.py`
- `services/api/tests/test_ruleset_connector.py`
- 본 Work Order 진행·결과 문서

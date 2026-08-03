# R1-M8-08 작업지시서 — 생산 지식 등록

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-08` |
| Issue ID | `R1-M8-08-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §7.5, §14, §16 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-08_progress.md` |

## 목적

사용자가 명시적으로 승인한 산출물만 불변 SourceVersion 계보와 함께 생산 지식으로 등록한다.

## 계약

- 등록은 명시적 사용자 명령과 추가 인증(`step_up`)이 모두 필요하다.
- 원본·Run·Model·검토 계보와 SourceVersion을 보존한다.
- 동일 SourceVersion 재등록과 순환 참조는 거부한다.
- 현재 권한이 축소되면 등록하지 않는다.
- 자동 Daon 승격은 수행하지 않는다.

## 허용 변경 파일

- `services/api/src/daon_user_api/knowledge_registration.py`
- `services/api/tests/test_knowledge_registration.py`
- 본 Work Order 진행·결과 문서

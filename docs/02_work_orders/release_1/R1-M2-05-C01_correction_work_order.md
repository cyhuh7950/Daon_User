# R1-M2-05-C01 수정 작업지시서

## 1. 판정과 범위

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M2-05` |
| issue_id | `R1-M2-05-I001` |
| 판정 | `INCOMPLETE 1/3` · S2 Major · 정식 `FAILURE_REPORT 0` |
| 재작업 | 승인 종료 전이·생산 지식 등록 전이·모바일 전체 Matrix·편집 Cursor 보존 |
| 개발자 | 동일 어울2 · 단일 Writer |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M2-05_progress.md` 계속 사용 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-05_attempt-2.md` |

원 작업지시서와 승인 정본은 그대로 유효하다. 기능 범위·공개 API·데이터·보안 경계는 변경하지 않는다. Attempt 1 산출물을 보존하고 아래 네 결함만 최소 보정한다.

## 2. 필수 보정

### C2-1 종료된 ApprovalRequest 불변성

- `pending` 요청만 `approved | rejected | expired | withdrawn`으로 전환할 수 있다.
- `expired | withdrawn | approved | rejected` 요청에 대한 후속 approve/reject/expire/withdraw Action은 기존 요청과 OutputVersion을 바꾸지 않고 안정적 오류(`APPROVAL_REQUEST_NOT_PENDING`)를 반환한다.
- expired/withdrawn 후 승인받으려면 반드시 새 ApprovalRequest를 생성해야 한다.
- 종료 전·후 상태와 OutputVersion·Audit 계보를 실제 배열/객체로 보존한다. `auditPreserved: true` 표식만으로 완료하지 않는다.
- 회귀 Test는 최소 `expired→approve`, `withdrawn→approve`, `approved→reject`, 새 요청→approve를 검증한다.

### C2-2 KnowledgeRegistration 전체 전이

- `KnowledgeRegistration: requested → registered | rejected`를 순수 Action으로 구현한다.
- `requested`만 완료로 보지 않는다. `register-knowledge`, `reject-registration` 등 안정적 Action과 안전 Code를 둔다.
- 등록 성공은 요청에 고정된 특정 OutputVersion ID를 불변으로 보존하고, 이후 Output 편집이 등록 Version을 바꾸지 않음을 Test한다.
- rejected 요청을 registered로 덮어쓰지 못하며 재요청은 새 KnowledgeRegistration ID를 만든다.
- Prototype 외부 효과는 계속 0이다. `registered`는 상태 Fixture이며 실제 Index 쓰기나 Daon 쓰기 성공으로 표시하지 않는다.

### C2-3 모바일 15개 작업 Matrix 완전성

- Domain의 모바일 필수 작업 15개를 UI Matrix에 모두 표시한다.
- 허용 Content 3개: `edit_title`, `edit_text_block`, `edit_simple_table_cell`.
- 허용 비Content 6개: `review_comment`, `request_revision`, `approve`, `reject`, `handle_notification`, `open_citation`.
- 차단 6개: `change_section`, `change_layout`, `change_table_structure`, `change_evidence_link`, `change_generation_settings`, `regenerate_all`.
- UI 목록은 Domain 정본에서 파생하거나 Test로 15개 집합 동일성을 고정해 재드리프트를 막는다.
- 각 항목은 허용/차단, 상태 Domain, Content Revision 생성 여부, 안전 Code/이어서 작업을 표시한다.

### C2-4 편집 Cursor 상태 보존

- Studio 편집 Cursor Control의 `value`를 상수가 아닌 `StudioWorkflowViewState` 정본에 연결한다.
- 사용자 변경 직후, Pane 언마운트·재마운트, 1920→1200→800→500 Projection 뒤에도 선택 Section/Cursor가 유지돼야 한다.
- Reducer Action과 UI Event를 연결하고 Test에서 state 값과 Rendered `select value`를 함께 검증한다.

## 3. TDD·검증

1. 네 결함을 각각 독립 회귀 Test로 추가하고 기존 구현에서 유효 RED를 확인한다.
2. 최소 Domain/UI 변경으로 Green을 만든다.
3. 전용·Workspace·Source·Run·Foundation 전체 회귀, Lint, Production Build, 공통 Gate를 재실행한다.
4. 새 Production Browser 세션에서 1200×900 승인 종료/새 요청/등록 전이, 800×900 모바일 15개 Matrix, 500×900 Cursor 보존을 실제 클릭한다.
5. Browser JSON·관련 PNG·Evidence Manifest·진행 기록·Attempt 2 보고서를 새 결과로 갱신한다. 변경되지 않은 1920 증거를 재사용하려면 실제 Hash·현재 구현 정합을 대조하고 사유를 기록한다.
6. Resource Timing이 없으면 `unavailable`로 기록하고 0으로 환산하지 않는다.

## 4. 완료 조건

- 종료 ApprovalRequest 덮어쓰기 재현 0건, 새 요청 경로 PASS
- KnowledgeRegistration requested→registered/rejected와 등록 Version 불변 PASS
- 모바일 Domain/UI Matrix 15/15 일치
- Cursor state와 Rendered Control의 재마운트·네 폭 보존 PASS
- 기존 정상 계약과 Prototype 정직성 회귀 0건
- 전체 자동 Test·Lint·Build·Gate PASS, 새 Browser 증거·Manifest Hash 일치
- 보호 Dirty 2개, API/DB/Dependency/Lockfile/CI 설정 무변경

완료 후 첫 줄은 다음 형식으로 보고한다.

```text
COMPLETED | R1-M2-05-I001 | C01 네 계약 보정 요약 | 변경 파일 | 테스트 근거 | 남은 위험 | 어울1 재검토
```

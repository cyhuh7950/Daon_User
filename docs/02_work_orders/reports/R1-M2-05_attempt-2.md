COMPLETED | R1-M2-05-I001 | C01 네 계약 보정: 종료 ApprovalRequest 불변·등록 전체 전이·모바일 15/15·Cursor 상태 보존 | Studio Model·Pane·Test·Architecture·Browser JSON·PNG 3개·Progress·Manifest | C01 4/4, 전용 17/17, 전체 77/77, Build·Quality Gate·Browser PASS | 실제 API·DB·Index·Native Gateway·기기 검증은 M4/M5/M6/M8 후속 | 어울1 재검토

# R1-M2-05 C01 Attempt 2 결과보고

## 판정

`COMPLETED` — C01 수정 작업지시서의 네 결함을 승인 범위 안에서 최소 보정했고 자동 회귀·Production Build·공통 Gate·새 Browser 증거를 완료했다. Commit·Push·서버·Merge는 수행하지 않았다.

## 네 계약 보정

1. 종료 ApprovalRequest 불변
   - `pending`만 `approved | rejected | expired | withdrawn`으로 종료한다.
   - 종료 요청의 후속 Action은 `APPROVAL_REQUEST_NOT_PENDING`을 반환하며 기존 요청·OutputVersion을 바꾸지 않는다.
   - 요청별 `audit` 배열에 pending과 종료 Event 객체를 보존하고, 새 ID 요청만 다시 승인한다.
2. KnowledgeRegistration 전체 전이
   - `requested → registered | rejected` Action과 `KNOWLEDGE_REGISTRATION_NOT_REQUESTED` Guard를 구현했다.
   - `knowledgeRegistrations` 이력 배열과 현재 투영을 분리하고 특정 OutputVersion ID를 고정한다.
   - 등록 후 Output 편집도 등록 Version을 바꾸지 않으며 실제 Index·Daon 쓰기는 0건이다.
3. 모바일 15개 Matrix
   - `MOBILE_STUDIO_ACTIONS` 단일 정본에 Content 3, 비Content 6, 차단 6을 고정했다.
   - UI 15개 모두 허용/차단, 상태 Domain, Content Revision 여부, 안전 Code, 이어서 작업을 표시한다.
4. Cursor 보존
   - `StudioWorkflowViewState.cursor`와 `set-cursor` Reducer Action을 추가했다.
   - 편집 `select`를 Controlled value로 연결해 Pane 재마운트와 1920→1200→800→500 Projection에서 보존한다.

## TDD·자동 검증

| 검증 | 결과 |
| --- | --- |
| C01 최초 RED | 기존 13 PASS / C01 4 FAIL, 결함별 원인 일치 |
| 전용 Green | 17/17 PASS |
| Studio·Workspace·Workspace Lint·Source·Run·Foundation | 77/77 PASS |
| Production Build | Exit 0, Compile·TypeScript·Static Page 3/3 PASS |
| `npm run verify:quality-gate` | Exit 0, Overall PASS, failures 0; lint·type·unit·contract·build·security·independence PASS |
| `git diff --check` | 오류 0건 |
| 금지 Browser URL·직접 fetch | 0건 |

Quality Gate가 자동 갱신한 범위 밖 M1-05 증거 2개는 정확한 경로만 원복했다. 보호 Dirty M1-04 `dependency-graph.json`, `violations.json`은 수정·복원·Stage하지 않았다.

## Production Browser

- 1200×900: `approval-request-001` 만료 뒤 approve 차단과 상태 불변, `approval-request-002` 새 요청 승인, 등록 1차 rejected·2차 registered, 고정 OutputVersion·이력 2건·외부 쓰기 0건을 확인했다.
- 800×900: 모바일 Domain/UI Matrix 15/15 정확한 순서·필드 표시와 `edit_text_block` Content Revision을 확인했다.
- 500×900: Cursor를 `section-3:table-1`로 바꾸고 자료·지식 전환 뒤 Studio 재마운트와 bottom-tabs Projection 후 Rendered `3절 · 표 1` 보존을 확인했다.
- 변경되지 않은 1920 Tile·설정·잠금 증거는 현재 구현과 기존 PNG SHA-256을 재대조해 재사용했다.
- Console warning/error 0/0이다. Resource Timing은 Browser 평가 문맥에서 unavailable이며 요청 0건으로 환산하지 않았다.

## 변경 결과

- 수정: `packages/ui/src/studio-workflow-model.js`, `packages/ui/src/studio-workflow-pane.jsx`, `scripts/tests/studio-workflow.test.mjs`
- 갱신: `docs/01_architecture/studio_workflow_prototype_adapter_contract.md`, Browser JSON, Evidence Manifest, 진행 기록
- 신규: `studio-c01-approval-registration-1200x900.png`, `studio-c01-mobile-15-800x900.png`, `studio-c01-cursor-500x900.png`, 이 Attempt 2 보고서
- Attempt 1 보고서와 기존 증거는 보존했다.

## 미해결 사항·다음 판단

- `registered`와 승인·전달은 Prototype Fixture 상태다. 실제 API·DB·파일·Delivery·Index·Gateway·실기기는 M4/M5/M6/M8 `deferred_actual`이며 PASS에 포함하지 않았다.
- 실행 기준은 `6eeb5b71723bd3df27274bd6cf2faf29a4376374` 위 미Commit Worktree다.
- 어울1은 Diff·증거를 읽기 전용 재검토한 뒤 S9 Commit·Push·격리 서버 검증 여부를 판단해야 한다.

# Studio 업무 흐름 Prototype·Adapter 계약

## 1. 목적과 경계

R1-M2-05는 운영형 Studio 사용자 여정을 클릭 가능한 순수 UI Prototype으로 검증한다. 이 단계는 공개 API, 영속 데이터 계약, Provider·LLM 실행, 실제 파일 생성·전달, 지식 Index 쓰기를 확정하거나 성공으로 주장하지 않는다.

- 현재 실행 주체: Browser의 React View와 순수 Reducer
- 현재 데이터: 코드에 고정된 결정론적 Fixture
- 실제 외부 효과: API 0건, DB 0건, LLM·Provider 0건, 파일 Export 0건, Delivery 0건, Knowledge Index 0건
- 후속 책임: M3 Client Shell, M4 권한·Gateway·API, M5 파일 저장, M6 지식 등록, M8 실제 Studio E2E

## 2. 소유권과 선행 계약 재사용

| 영역 | 파일 | 소유·재사용 계약 |
| --- | --- | --- |
| Studio Domain | `packages/ui/src/studio-workflow-model.js` | R1-M2-05 소유. 생성 설정 Snapshot, OutputVersion 계보, 검토·승인, AccessDecision, 모바일 Allowlist를 순수 상태 전이로 제공한다. |
| Studio View | `packages/ui/src/studio-workflow-pane.jsx` | R1-M2-05 소유. Domain 상태를 표시하고 명시 사용자 작업만 Reducer Action으로 전달한다. |
| Workspace 연결 | `packages/ui/src/adaptive-workspace.jsx`, `workspace-model.js` | M2-02의 Layout·Projection·상태 보존을 재사용하며 `studio_workflow` Slice와 Action 연결만 추가한다. |
| Evidence Viewer | M2-03 기존 Component | SourceVersion·Citation 열기 동작을 재사용한다. 별도 근거 Viewer를 만들지 않는다. |
| Source·권위 | M2-03 기존 Fixture·Snapshot | SourceVersion, RuleSet, 권위 순서, Clamp 표시 값을 재사용한다. |
| Run·계보 | M2-04 기존 Run 계약 | GenerationSettingsSnapshot의 Request·Run·Output 연결과 실제 실행 후속 경계를 표시한다. |

## 3. Production-bound Adapter 경계

| 유지할 계약 | M3·M4·M5·M6·M8에서 교체·연결할 부분 | 금지 사항 |
| --- | --- | --- |
| `createStudioViewState`, `transitionStudioViewState`의 명시 Action과 안전 Code | M8 API 응답을 Domain Action으로 변환하는 Adapter | Component 내부에서 API 절대주소 또는 내부 Host를 호출하지 않는다. |
| `GenerationSettingsSnapshot`의 깊은 불변성과 제출 시점 동결 | M8 저장 API·Run ID·실제 Timestamp Adapter | 제출 뒤 기존 Snapshot을 가변 수정하지 않는다. |
| OutputVersion 이전 Version·Revision 이유·근거 계보 | M5 실제 파일 Artifact와 M8 영속 Version Adapter | Fixture Version을 실제 저장 성공으로 표시하지 않는다. |
| ReviewRequest와 ApprovalRequest 분리, 반려·만료·회수·재승인 | M4 권한·알림·Audit API와 M8 실제 계정 흐름 | UI 숨김만으로 권한을 강제했다고 주장하지 않는다. |
| 요청별 `AccessDecision`과 `CURRENT_ACCESS_DENIED` | M4 Membership·ACL·SourceVersion·조직 정책 Gateway | 과거 승인이나 이전 요청의 AccessDecision을 재사용하지 않는다. |
| `evaluateMobileAction` Allowlist와 안정적 안전 Code | M3 Native Shell, M4 Native Gateway, M8 Android·iOS | 차단 작업을 Client 우회로 허용하지 않는다. |
| Export·Delivery·KnowledgeRegistration Preview | M5 파일, M4 Delivery, M6 Index Adapter | Preview를 실제 Export·전달·등록 완료로 표시하지 않는다. |

Adapter는 같은 Origin의 BFF·Route Handler 경계 뒤에 둔다. Browser 실행 코드는 상대 경로만 사용하며 `localhost`, `127.0.0.1`, Docker 내부 Host·Port, `NEXT_PUBLIC_API_BASE_URL`을 직접 소비하지 않는다.

## 4. Fixture 교체 순서

1. M3는 현재 Pane과 Domain State를 Client Shell에 연결하되 모바일 Allowlist 결과를 변경하지 않는다.
2. M4는 역할·현재 접근·승인·전달 결정을 서버 Gateway에서 다시 강제하고 안전 Code를 대조한다.
3. M5는 Export Preview를 실제 Artifact 생성·저장·다운로드 계약으로 교체한다.
4. M6는 명시 KnowledgeRegistration만 Index Adapter에 연결하며 자동 등록은 계속 금지한다.
5. M8은 Fixture 생성·검토·승인·전달·등록을 실제 API·DB·계정·파일·기기 흐름으로 교체하고 Browser Network와 응용프로그램 Open까지 검증한다.

각 교체 단계는 순수 Domain Test를 유지하고, 실제 Adapter Test를 별도로 추가한다. 공개 API·데이터 계약·보안 경계가 이 문서의 내부 구현 가정을 넘어서는 경우 구현하지 않고 어울1의 설계 판단과 신산님의 승인 경계를 따른다.

## 5. 관측과 정직성

Prototype 화면은 `Production-bound Prototype`, `unavailable`, 실제 외부 효과 0건, 후속 책임 웨이브를 함께 표시한다. Browser Resource Timing을 도구 문맥에서 읽을 수 없으면 `unavailable`과 원인을 기록하며 요청 0건으로 환산하지 않는다. 실제 성공 판정은 후속 Wave의 API·DB·Network·파일·기기 증거가 있을 때만 가능하다.

## 6. C01 종료 상태·모바일·Cursor 보정 계약

- ApprovalRequest의 `audit` 배열은 생성과 종료 Event를 실제 객체로 보존한다. `pending` 이외 요청에 대한 종료 Action은 `APPROVAL_REQUEST_NOT_PENDING`으로 차단하며, 새 ID의 요청만 다시 승인할 수 있다.
- `knowledgeRegistrations` 배열은 모든 요청을 보존하고 현재 `knowledgeRegistration`을 화면에 투영한다. `requested`만 `registered | rejected`로 종료할 수 있고, 등록된 요청은 당시 `outputVersionId`를 계속 가리킨다. `registered`는 Fixture 상태이며 외부 Index·Daon 쓰기는 0건이다.
- `MOBILE_STUDIO_ACTIONS`는 허용 Content 3개, 허용 비Content 6개, 차단 6개의 단일 Domain 정본이다. UI는 이 15개를 직접 순회하며 상태 Domain, Content Revision 여부, 안전 Code, 이어서 작업을 함께 표시한다.
- `StudioWorkflowViewState.cursor`가 편집 Cursor의 정본이다. `set-cursor` Action과 Controlled `select`를 사용하므로 Pane 재마운트와 네 폭 Projection이 값을 초기화하지 않는다.

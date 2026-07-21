# Source·Authority Prototype Adapter 승계 계약

## 판정

`R1-M2-03`은 M3가 재사용할 자료·지식 Pane의 Production-bound 표현·순수 상태 계약이다. 실제 Upload·API·DB·Queue·Index·LLM·Connector 실행은 포함하지 않으며 모든 미연결 동작은 `프로토타입 데이터` 또는 `unavailable`로 공개한다.

## 소유 경계

| 경계 | 소유 | 계약 |
| --- | --- | --- |
| Source·Version·Modality·권위·가중치·RuleSet·충돌 순수 모델 | `packages/ui/src/source-knowledge-model.js` | Network·Browser 전역 없이 결정론적으로 실행한다. |
| Source List·Detail·처리·권위·충돌 표현 | `packages/ui/src/source-knowledge-pane.jsx` | M2-02 `AdaptiveWorkspace`의 자료·지식 Pane Domain View로 소비된다. |
| Layout·Pane·Drawer·Evidence Viewer·Focus | M2-02 `AdaptiveWorkspace` 계약 | R1-M2-03이 재정의하지 않고 `selected_source_id` 전이만 확장한다. |
| Prototype Seed | `createSourcePrototypeSeed()` | M3 Adapter로 교체 가능하며 Production 성공 데이터로 사용하지 않는다. |

## Source·처리 계약

- 화면 지식 원천은 `user_material | internet | llm_knowledge | daon_approved | produced_knowledge` 다섯 타입이며 RuleSet은 `ruleset` 별도 정책 타입이다.
- SourceVersion은 불변이고 변경은 `previousVersionId`를 가진 새 Version으로만 만든다.
- 문서·표·이미지는 `Vision/LLM-first → Parser/OCR 검증·보완 → Evidence reconciliation → Indexing` 순서를 표현하며 Parser/OCR-only `ready`를 허용하지 않는다.
- 오디오는 `Audio LLM 직접 이해` 또는 `ASR → LLM 의미 이해` 두 경로를 사용하고, 모두 시간 구간 검증·Evidence reconciliation·Indexing을 통과해야 `ready`다.
- 정책 후보 0은 ProcessingRun `policy_blocked`와 Source `needs_review`, Runtime 후보 소진은 `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`과 Source `waiting_model`로 구분한다.
- `waiting_model`의 실제 자동·수동 새 Run은 M2-07 소유이므로 R1-M2-03에서는 `unavailable` 진입 자리만 제공한다.

## 권위·가중치·RuleSet 계약

- 권위 순서는 강제 RuleSet 조건 → Daon 승인 지식 → 사용자 맥락 지식 → 출처 확인 인터넷 → LLM 일반지식이다.
- 가중치는 `0.5~2.0`, `0.1` 단위, 기본 `1.0`이다. 개별 Source → 그룹 → 유형 → 기본값 중 하나만 적용하고 곱하지 않는다.
- 조직 범위 Clamp는 요청값·적용값·적용 계층·사유를 Snapshot Preview로 공개한다.
- 가중치는 같은 권위 Tier 안에서만 작동하며 Source 제외는 별도 활성화 상태다.
- 강제 RuleSet은 잠겨 해제할 수 없다. 선택형은 `warn_and_skip | block` 실패 방식을 함께 표시한다.

## 충돌·최종화 계약

- `informational | material | critical`을 모두 공개하고 자동 `ConflictPolicyVersion` 판정과 관련 SourceVersion·적용·배제 사유를 표시한다.
- 검토자는 심각도를 상향할 수 있으나 정책으로 잠긴 중요도를 낮추지 못한다.
- 미해결 `material | critical`이 있으면 `review_required=true`이며 승인·외부 전달·생산 지식 등록을 차단한다.
- Prototype 해결은 UI 상태 전이와 Audit Preview만 만들며 실제 승인·전달·등록을 실행하지 않는다.

## M3 교체 지점

M3는 `SourceKnowledgePane`과 순수 모델을 유지한 채 Seed 공급을 Domain Adapter로 교체한다. M4 이후 Browser 데이터 연결은 same-origin BFF 상대 경로만 사용한다. Provider URL·내부 Host·Secret·`NEXT_PUBLIC_API_BASE_URL`은 Component·상태·Client Adapter에 둘 수 없다.

## 회귀 경계

- M2-01 Route·Screen·Token·접근성 정본을 수정하지 않는다.
- M2-02 Breakpoint·Resize·Pane·Drawer·Bottom Tab·Evidence Viewer·Modal Focus 계약을 유지한다.
- 새 Runtime Dependency·Lockfile·Backend/API/DB/Auth를 추가하지 않는다.
- 1920×1080과 1200/800/500 대표 폭에서 Source List → Detail → 처리 → 권위/가중치 → 충돌 흐름을 Keyboard와 Pointer로 사용할 수 있어야 한다.

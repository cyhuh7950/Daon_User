# NotebookLM형 작업지원 워크스페이스 재설계

## 1. 재설계 목적

이 제품은 문서 내용을 검색하는 단순 Q&A가 아니라, 사용자가 선택한 Notebook과 Source를 작업의 근거로 삼아 조사·상담·정리·실행을 돕는 운영형 연구 워크스페이스다. 사용자는 대화창에서 작업 진행, 다음 단계, 아이디어, 계획, 검토를 자연어로 상담하고, 필요할 때만 “선택한 Source에 이 내용이 있는가”를 명시적으로 확인한다. 오른쪽 업무 Studio는 Source 근거를 이용해 보고서·표·체크리스트·구조도 등 결과물을 만드는 실행 영역이다.

기존 구현의 핵심 오해는 세 가지다.

1. 대화창을 Source 문서 질의 전용으로 취급해 일반 작업 상담을 `근거가 부족하여 답변할 수 없습니다`로 종료했다.
2. Source 목록을 불러오지 못한 상태와 대화·LLM 상태를 섞어 표시했다.
3. Studio가 Source 기반 산출물을 만드는 영역이라는 경계를 UI와 프롬프트에 일관되게 반영하지 못했다.

재설계는 위 세 문제를 제품 계약, 상태 모델, 프롬프트, 검증 게이트로 분리해 해결한다.

## 2. NotebookLM 기준을 제품에 적용하는 방식

공식 Gemini Notebook 안내에 따라 Source는 가져온 문서의 복사본·동기화본이며 모델이 답변과 작업 수행의 근거로 사용한다. Source를 선택하면 그 범위로 대화를 좁힐 수 있고, 인용은 Source의 직접 텍스트·이미지·위치를 가리킨다. 따라서 이 제품의 “Source 기반”은 사실을 임의로 생성하지 않는다는 뜻이지, 모든 대화를 문서 요약 질문으로 제한한다는 뜻이 아니다.

적용 원칙은 다음과 같다.

- 기본 대화 모드: 사용자의 작업 진행을 상담하고, 계획을 정리하고, 다음 행동을 제안한다. 선택 Source를 활용할 수 있으면 답변의 관련 부분에 Citation을 붙인다.
- 명시적 Source 확인: “이 Source에 … 있어?”, “문서에서 찾아줘”처럼 요청하면 선택된 Source 범위만 검색하고 Citation을 반환한다.
- 근거 범위 불일치: 현재 Source가 다루는 범위와 질문의 불일치를 설명한다. 답변을 빈 거절문으로 끝내지 않고 Source 추가·선택 변경·승인된 웹 조사 중 가능한 다음 행동을 안내한다. Source에 없는 사실을 Source 근거인 것처럼 말하지 않는다.
- 외부 최신 정보: 웹 조사가 제품 정책상 허용되고 사용자가 승인한 경우에만 별도 Web Research 경로를 사용한다. 기본 Source 모드가 자동으로 웹을 호출하지 않는다.
- Studio: 선택 Source의 Evidence와 Citation을 입력으로 받아 결과물을 생성하고, 지원되지 않는 주장은 “확인 필요”로 표시한다. 생성 결과와 사용한 SourceVersion·EvidenceSpan·Prompt/Run을 Library에 저장한다.

## 3. 사용자 흐름과 화면 경계

### 3.1 초기 진입

`Home → Notebook 선택 → 로그인/세션 확인 → 3열 Workspace` 흐름을 유지한다. 세 열은 다음과 같이 독립적으로 로드한다.

| 영역 | 책임 | 실패 표시 |
|---|---|---|
| Source·지식·권위 | Notebook에 연결된 Source 목록·처리 상태·선택·추가·Notebook 제거 | Source 영역만 오류/재시도 |
| 대화·실행 | 작업 상담, Source 확인, 실행 지시, LLM 응답 | 대화 영역만 오류 |
| 업무 Studio | Source 기반 산출물 생성·진행·Library 저장 | Studio 영역만 오류 |

`Cloud 미확인`은 LLM 연결 실패를 의미하지 않는다. 운영상태 조회가 아직 끝나지 않았거나 상태를 알 수 없는 경우에만 `상태 확인 중`으로 표시하고, 확인 결과를 `연결됨·연결 불가·구성 필요·권한 없음`으로 구분한다.

### 3.2 Source 수명주기

Source 추가는 실제 PDF 업로드를 1차 범위로 하고, URL·YouTube·Docs·Slides·오디오·이미지는 후속 타입으로 확장 가능한 `source_type` 계약을 사용한다. 상태는 `uploading → processing → ready | failed`이며 목록 응답은 각 상태와 `source_id`, `source_version_id`, filename, version, error code를 제공한다.

Source를 Notebook에서 제거하는 것과 원본 삭제 요청은 분리한다. 제거는 append-only unbinding으로 현재 Notebook의 질의·Studio에서만 제외하고 원본은 보존한다. 삭제 요청은 기존 유예·Legal Hold·복구 계약을 사용한다.

초기 목록 조회 실패는 상태를 빈 목록으로 덮어쓰지 않는다. 네트워크 TypeError 등 안전한 transient 오류만 bounded retry 1회 후 실패하며, 4xx/5xx·계약 오류는 원인 코드와 재시도 가능 여부를 보존한다. retry 클릭은 Source reducer만 갱신한다.

### 3.3 대화·실행

대화창의 기본 안내문은 “선택한 Source와 작업 맥락을 바탕으로 무엇이든 물어보세요”로 한다. 질문 라우터는 `work_support`, `explicit_source_lookup`, `source_backed_action`, `approved_web_research` 네 모드로 분류한다.

- `work_support`: 계획·진행상황·검토·아이디어·사용법·다음 행동. 선택 Source가 관련되면 검색하고, 없으면 일반 LLM 상담으로 답한다. 답변에는 `작업 상담 · Source 사용` 또는 `작업 상담 · 근거 미사용`을 표시한다.
- `explicit_source_lookup`: Source 검색 결과만 사용하고 Citation을 필수화한다.
- `source_backed_action`: “이 자료로 보고서를 만들어줘”처럼 Studio 또는 실행 작업으로 전달한다. 대화는 작업 범위·사용 Source·예상 결과를 확인하고 Studio 실행을 시작한다.
- `approved_web_research`: 사용자의 명시 승인 후에만 웹 검색을 수행하며 Source 근거와 웹 근거를 구분한다.

현재 Source에 없거나 질문 범위가 불명확하면 모델은 다음 구조로 답한다.

1. 현재 선택 Source가 다루는 주제 요약
2. 질문과 Source 범위의 불일치
3. 가능한 다음 행동(다른 Source 선택, Source 추가, 승인된 웹 조사, 질문 재구성)

`근거가 부족하여 답변할 수 없습니다`를 단독 사용자 응답으로 사용하지 않는다. 다만 명시적 Source 확인이나 Source 기반 Studio 산출물에서 증거가 없으면, 위 설명과 함께 `source_evidence_unavailable` 상태를 저장하고 결과물의 미확인 항목으로 표시한다.

### 3.4 업무 Studio

Studio 카드는 Source를 선택한 뒤 생성할 수 있다. 생성 요청은 `notebook_id`, 선택 SourceVersion 집합, artifact type, 사용자 지시, 현재 Run/Prompt 버전을 포함한다. 서버는 Evidence 검색·Citation projection·외부 전송 정책을 재검증하고, 미지원 주장은 생성하지 않고 `verification_required`로 남긴다. 저장된 산출물은 Library에서 원본 Source와 lineage를 다시 열람할 수 있어야 한다.

## 4. 서버·API 계약

기존 same-origin BFF와 공개 DTO를 유지하고, 아래 필드를 명시한다.

```json
{
  "source": {
    "source_id": "src-1",
    "source_version_id": "ver-1",
    "source_type": "pdf",
    "filename": "guide.pdf",
    "status": "ready",
    "error_code": null
  },
  "conversation": {
    "mode": "work_support",
    "grounding": "source_backed | ungrounded | source_evidence_unavailable | web_backed",
    "citations": []
  }
}
```

`GET /bff/api/workspaces/{workspace_id}/sources?notebook_id={notebook_id}`의 exact `{data:{sources},meta:{trace_id,workspace_id}}` 계약은 유지한다. 질문·Studio 응답은 `mode`, `grounding`, `citations`, `source_scope_summary`, `next_actions`를 제공하고, 기존 RunSnapshot·Provider·Egress·Idempotency·Audit 계보를 보존한다. 브라우저는 상대 경로만 호출한다.

공개 오류는 `SOURCE_LIST_UNAVAILABLE`, `SOURCE_UPLOAD_REJECTED`, `SOURCE_PROCESSING_FAILED`, `QUESTION_CONTEXT_INVALID`, `TEXT_PROVIDER_UNAVAILABLE`, `POLICY_PROJECTION_UNAVAILABLE`처럼 사용자 행동으로 연결되는 안전 코드만 반환한다. 내부 URL·SQLSTATE·stack·credential은 로그와 응답에 노출하지 않는다.

## 5. 프롬프트와 안전 규칙

Grounded prompt에는 선택 Source의 범위, Evidence JSON, Citation 허용 목록을 전달한다. 모델은 Source 밖의 내용을 Source 사실로 표시하지 않고, 범위 불일치 시 `source_scope_summary`, `mismatch`, `next_actions`를 반환한다. General/work-support prompt에는 Source를 사용했다고 거짓 주장하지 않으며, 작업 상담과 다음 행동을 자연어로 답한다. Studio prompt에는 입력 Evidence 외 사실을 만들지 않고 검증 필요 항목을 구조화한다.

LLM이 답변을 못하는 경우는 연결 상태·인증·정책·Provider 오류와 근거 범위 불일치를 별도 상태로 구분한다. Provider 자동 fallback은 유지하지 않는다.

## 6. 구현·테스트·브라우저 검증의 분리

기능 구현과 자동 테스트는 로그인 UI에 의존하지 않는다. 로컬·통합 테스트는 테스트 사용자/세션 주입 또는 인증 경계 mock을 사용해 Source·Conversation·Studio의 기능 계약을 먼저 검증한다. 운영 인증·권한 코드는 제거하거나 완화하지 않는다. 기능 계약과 자동 테스트가 통과한 뒤에만 로그인된 브라우저에서 최종 세션·권한·Network 검증을 수행한다.

## 7. 우선순위와 완료 게이트

P0는 실제 Source 기능이다. 다음 순서가 강제된다.

1. Source 초기 목록의 실제 브라우저 성공·실패 원인과 재시도 계약 확인
2. PDF 추가→처리→ready→목록 반영→Notebook 제거/삭제 요청
3. 작업 상담 대화와 Source 명시 질의의 라우팅·프롬프트·표시
4. Studio Source 기반 산출물·Citation·Library 저장
5. ysna-server 운영 유사 배포와 1920×1080 브라우저 검증

P0를 통과하기 전에는 프롬프트 개선이나 Studio 기능을 완료로 판정하지 않는다. 완료 조건은 실제 데이터 0 fixture, Source 추가·조회·삭제 흐름 성공, 작업 상담 자연어 응답, 명시 Source 질문 Citation, Studio lineage, 영역별 오류 격리, same-origin Network, build/type/test 통과다.

## 8. 비범위

- Source에 없는 사실을 Source 근거처럼 생성
- 사용자 승인 없는 웹 검색·Provider 자동 fallback
- 테스트 fixture를 운영 목록에 투영
- Source 제거를 원본 물리 삭제로 처리
- Notebook 선택 전 임의 Workspace 데이터 표시
- Oracle Cloud 운영 배포

## 참고한 공식 기준

- [Gemini Notebook 개요](https://support.google.com/gemininotebook/answer/16164461?hl=en)
- [Gemini Notebook에서 채팅하기](https://support.google.com/gemininotebook/answer/16179559?hl=en&ref_topic=16164070)
- [Gemini Notebook에 Source 추가](https://support.google.com/gemininotebook/answer/16215270)
- [Audio Overviews](https://support.google.com/gemininotebook/answer/16212820)

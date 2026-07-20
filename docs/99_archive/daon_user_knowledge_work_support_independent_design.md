# SUPERSEDED — Daon 사용자형 지식 업무지원 프로그램 구 독립 설계

> **구현 기준으로 사용 금지.** 이 문서는 Daon2 내부 모듈에 결합하던 폐기된 접근을 기록한 역사 자료다. 현행 구현 정본은 `docs/superpowers/specs/2026-07-20-daon-user-program-design.md`이다. Vision/LLM-first와 근거·보안·계보 원칙만 현행 정본에 명시적으로 계승되며, 기존 Daon2 모듈·DB·`DEV-U` 번호·same-origin 전용 구조는 계승하지 않는다.

보관일: 2026-07-20  
대체 정본: `docs/superpowers/specs/2026-07-20-daon-user-program-design.md`

# Daon 사용자형 지식 업무지원 프로그램 독립 설계

작성일: 2026-07-19  
대상 버전: Daon2 2.0, Daon2.5, Daon3.0  
상태: 독립 제품 설계 기준안  
제품명(설계용): Daon Work Studio  
참조 UX: 사용자 제공 NotebookLM 화면(2026-07-19)

## 1. 설계 결정

### 1.1 결론

사용자형 프로그램은 관리자 콘솔이나 전문가 화면의 하위 메뉴가 아니라 독립 제품으로 설계한다.

사용자형 프로그램은 관리자·전문가 시스템의 완성을 기다리지 않고 다음 범위를 병렬 개발할 수 있다.

- 개인 또는 현재 업무 자료를 모으는 워크스페이스
- 자료의 Vision LLM-first 이해 상태와 원문 근거 확인
- 선택한 자료를 근거로 한 질문, 요약, 비교, 제약 점검과 문서 생성
- 보고서, 점검표, 데이터 표, 지식 구조도, 업무 문서 초안의 생성과 버전 관리
- same-origin BFF를 통한 로그인, 상태, 실행, 결과 조회

관리자와 전문가가 발행하는 공용 지식은 사용자형 프로그램의 선행 조건이 아니라 후속 결합 자산이다. 사용자형 프로그램은 초기에는 개인 워크스페이스 자료만으로 동작하고, 공용 지식이 준비되면 동일한 지식 범위 선택 계약으로 결합한다.

### 1.2 제품 정의

Daon Work Studio는 사용자가 업무 자료와 승인된 조직 지식을 선택하고, 그 범위 안에서 질문·점검·비교·분석·문서 생성을 수행하는 근거 중심 업무 공간이다.

NotebookLM의 `자료 - 대화 - 스튜디오` 경험을 참고하되 그대로 복제하지 않는다. Daon은 여기에 프로젝트·업무 패키지, RuleSet, 제약 점검, 검토 요청, 전달, 버전, 감사와 실행 복구를 결합한다.

### 1.3 독립 개발이 가능한 이유

사용자형 프로그램의 핵심 입력은 공용 RAG 자체가 아니라 표준화된 `SourceRef`, `KnowledgeScope`, `Run`, `Evidence`, `StudioOutput` 계약이다. 이 계약은 개인 자료만으로도 구현·검증할 수 있다.

관리자·전문가 시스템과 공유하는 것은 다음의 발행 결과뿐이다.

- 관리자가 발행한 문서 지식 패키지와 검색 인덱스 버전
- 전문가가 승인한 Expert RuleSet, Boundary, Case와 전문가 지식 패키지
- 프로젝트·업무 패키지 권한과 유효 버전
- 검토·전달·감사 상태 계약

## 2. 역할과 책임 경계

| 제품/역할 | 핵심 책임 | 사용자형 프로그램과의 관계 | 금지 경계 |
| --- | --- | --- | --- |
| 관리자 콘솔 | 시스템·모델·엔진 운영, 문서 이해 품질, 공용 문서 RAG와 패키지 발행 | 승인된 공용 지식과 상태를 제공 | 전문가 판단을 임의 확정하지 않음 |
| 전문가 화면 | 근거 검토, 판단·예외·사례 구조화, Expert RuleSet과 전문가 RAG 승인 | 승인된 전문가 지식과 규칙을 제공 | 시스템 비밀값과 운영 인프라를 직접 관리하지 않음 |
| 사용자형 프로그램 | 개인 자료와 승인 지식을 선택해 질문·점검·분석·문서 생성 | 실행 범위를 선택하고 개인 산출물을 관리 | 공용 RAG·RuleSet을 직접 편집하거나 자동 승격하지 않음 |

사용자가 자료 분석을 요청하면 플랫폼이 자동으로 이해·청킹·임베딩을 수행할 수 있다. 그러나 사용자는 벡터 인덱스의 내부 구조, 모델 연결, 청킹 정책이나 공용 발행 상태를 직접 운영하지 않는다.

## 3. 불변 원칙

1. Vision LLM-first는 Daon2, Daon2.5, Daon3에서 영구 불변이다.
2. Parser/OCR와 Upstage Document Parse는 명시된 보조, fallback 또는 교차 검증 경로다.
3. 브라우저는 same-origin 상대 경로만 호출한다.
4. Provider URL, 내부 API 주소, Docker host, API Key와 secret은 브라우저에 노출하지 않는다.
5. 개인 자료, 공용 문서 지식, 전문가 지식은 소유권과 발행 상태를 분리한다.
6. 개인 자료와 사용자 산출물은 검토 없이 공용 RAG·RuleSet으로 자동 승격하지 않는다.
7. 모든 답변과 산출물은 근거, 적용 규칙, 실행·버전 계보와 경고를 보존한다.
8. 근거가 부족하면 추측 답변으로 채우지 않고 `근거 부족` 또는 `확인 필요`로 표시한다.
9. 사용자에게 모델의 원문 Chain-of-Thought를 노출하지 않는다.
10. 화면은 DB·CLI 대신 상태, 다음 행동, 오류와 복구 방법을 제공한다.

## 4. 목표 사용자와 핵심 업무

| 사용자 유형 | 시작 상황 | 기대 결과 |
| --- | --- | --- |
| 일반 실무자 | 여러 문서에서 답을 찾아야 함 | 인용 가능한 근거 답변과 요약 |
| 점검 담당자 | 문서가 규정·요구·약관을 충족하는지 확인해야 함 | RuleSet·제약 조건별 점검표와 누락·위반 |
| 문서 작성자 | 기존 자료를 기반으로 새 보고서·제안서·업무 문서를 작성해야 함 | 근거와 템플릿이 연결된 초안과 버전 |
| 프로젝트 담당자 | 개인 자료와 조직 지식을 함께 사용해야 함 | 범위를 명시한 비교·분석·결과물 |
| 검토 요청자 | 결과를 전문가나 관리자에게 검토받아야 함 | 검토 상태, 수정 요청, 승인·전달 이력 |

## 5. NotebookLM 참고 범위와 Daon 차별점

| NotebookLM 참고 요소 | Daon 적용 | Daon 추가 가치 |
| --- | --- | --- |
| 출처 중심 작업공간 | 워크스페이스별 자료 목록과 선택 범위 | 개인 자료와 승인된 조직 지식을 분리·결합 |
| 근거 기반 채팅 | 인용·페이지·영역을 포함한 대화 | RuleSet·Boundary 점검과 실행 run 연결 |
| 스튜디오 산출물 | 보고서·표·점검표·문서 초안 | 검토·버전·다운로드·전달·감사 수명주기 |
| 노트북 단위 구성 | 프로젝트·업무 패키지·워크스페이스 | 권한, 모델·엔진 정책과 지식 패키지 버전 |
| 소스 추가 | Source Adapter 기반 등록 | Vision-first 상태, 실패·재처리·근거 품질 표시 |

Daon은 `AI가 자료를 요약해 주는 화면`이 아니라 `승인된 지식과 개인 자료를 근거로 실제 업무를 수행하고 결과를 관리하는 시스템`을 목표로 한다.

## 6. 정보 구조와 화면 체계

### 6.1 전역 내비게이션

- 홈: 최근 워크스페이스, 처리 중 자료, 실행 상태, 확인 필요 결과
- 워크스페이스: 내 워크스페이스와 공유받은 워크스페이스
- 전달함: 승인·전달된 결과와 받은 결과
- 이력: 질문, 점검, 생성, 검토, 다운로드와 전달 이력
- 알림: 처리 실패, 근거 부족, 검토 결과, 전달 완료
- 계정/설정: 개인 표시 설정과 접근 가능한 프로젝트·업무 패키지

### 6.2 워크스페이스 기본 화면

데스크톱은 다음 3면을 기본으로 한다.

| 면 | 목적 | 주요 기능 |
| --- | --- | --- |
| 자료·지식 | 이번 업무에 사용할 범위를 구성 | 자료 추가, 처리 상태, 출처 선택, 공용/전문가 지식 선택, 근거 열기 |
| 대화·실행 | 질문과 업무 요청을 수행 | 질문, 요약, 비교, 제약 점검, 추출, 문서 생성, 실행 상태, 재시도 |
| 업무 스튜디오 | 생성된 산출물을 관리 | 보고서, 점검표, 표, 지식 구조도, 문서 초안, 버전, 검토, 다운로드, 전달 |

모바일에서는 세 면을 탭으로 전환하고, 원문 근거는 전체 화면 또는 하단 시트로 연다. 데스크톱의 고정 3열을 축소해서 겹치게 만들지 않는다.

### 6.3 보조 화면

- 자료 상세: 원본, 버전, 페이지 상태, 의미 블록과 근거
- 실행 상세: run, 단계, 사용 지식 범위, 경고와 오류
- 산출물 상세: 버전 비교, 근거, 적용 RuleSet, 검토·전달
- 지식 범위 편집: 개인 자료, 공용 지식, 전문가 지식의 포함·제외
- 접근 권한: 공유 멤버와 읽기·실행·편집·전달 권한

## 7. 워크스페이스와 지식 범위

### 7.1 워크스페이스 정의

워크스페이스는 사용자의 현재 업무 문맥을 보존하는 독립 단위다. 프로젝트와 업무 패키지를 참조하지만 이를 대체하지 않는다.

워크스페이스는 다음을 소유한다.

- 제목, 설명, 소유자, 프로젝트, 업무 패키지
- 개인 자료 참조와 자료 버전
- 선택한 공용 지식 패키지·전문가 지식 패키지·RuleSet 버전
- 대화, 실행, 산출물, 검토·전달 이력
- 마지막으로 실행한 지식 범위 snapshot

### 7.2 지식 범위 종류

| 범위 | 소유자 | 사용 방식 | 공용 승격 |
| --- | --- | --- | --- |
| 개인 워크스페이스 자료 | 사용자/워크스페이스 | 해당 워크스페이스 검색 인덱스에서만 사용 | 자동 승격 금지 |
| 프로젝트 승인 문서 지식 | 관리자/프로젝트 | 발행된 패키지 버전을 참조 | 관리자 발행 절차 필요 |
| 전문가 지식·RuleSet | 전문가/업무 패키지 | 승인된 버전만 참조 | 전문가 검토·승인 필요 |
| 전달받은 산출물 | 결과 소유자/수신자 | 읽기 또는 명시적 재사용 | 원본 권한과 재사용 정책 필요 |

### 7.3 실행 snapshot

모든 요청은 실행 시점의 지식 범위를 immutable snapshot으로 저장한다.

- 포함한 source/version 목록
- knowledge package/version 목록
- RuleSet/Boundary version 목록
- retrieval profile, reranker, prompt와 model mapping version
- 사용자, 프로젝트, 업무 패키지, trace와 실행 시각

후속 지식 업데이트가 과거 답변의 근거를 바꾸지 않도록 기존 결과는 snapshot을 계속 참조한다.

## 8. 소스 모델과 수명주기

### 8.1 Source Adapter 계약

내부 계약은 소스 종류를 확장할 수 있도록 설계하되, 버전별 지원 범위를 분리한다.

| Source 유형 | Daon2 | Daon2.5 | Daon3 |
| --- | --- | --- | --- |
| PDF, DOCX, PPTX, XLSX, TXT/MD, 이미지 | 필수 | 고도화 | 유지 |
| 승인된 문서/전문가 지식 패키지 | 필수 | 고도화 | 유지 |
| 이전 전달 산출물 | 필수 | 고도화 | 유지 |
| URL snapshot | 안전 fetch gateway 준비 후 선택 | 필수 | 유지 |
| Drive/SharePoint/Email 등 connector item | 계약 준비 | 단계 도입 | 확장 |
| 오디오 transcript | 제외 | 선택 도입 | 필수 확장 |
| 동영상, live feed, 업무 시스템 event | 제외 | 계약 준비 | 선택 도입 |

`무엇이든 소스가 될 수 있다`는 것은 임의 실행 파일이나 브라우저 직접 fetch를 허용한다는 뜻이 아니다. 모든 소스는 allowlist, 권한, 악성 파일 검사, 크기·형식 제한, snapshot과 provenance를 통과해야 한다.

### 8.2 처리 흐름

1. 소스 등록과 권한·형식·무결성 검사
2. Object Storage 또는 승인된 외부 참조에 원본/version 저장
3. Vision LLM-first 문서 이해와 의미 청킹
4. Parser/OCR 보조 경로가 사용되면 fallback reason 기록
5. 의미 블록·표·근거 span 검증
6. Embedding과 workspace-private index 생성
7. 검색 가능 상태 전환
8. 실패 시 페이지·블록 단위 재처리 또는 수동 확인 안내

### 8.3 사용자 표시 상태

- 등록 중
- 업로드 완료
- Vision 분석 중
- 검색 준비 중
- 사용 가능
- 일부 페이지 확인 필요
- 보조 분석 사용
- 실패
- 재처리 대기
- 접근 권한 만료

내부 canonical 상태는 기존 document_registry, document_understanding, workflow_runtime 계약을 사용하고, 사용자 화면은 이해 가능한 표시 문구로 매핑한다.

## 9. Workspace RAG와 검색 계약

### 9.1 검색 구성

사용자 요청은 다음 검색 범위를 조합한다.

- workspace-private vector/keyword index
- 관리자 발행 문서 지식 index
- 전문가 발행 지식 index
- 구조화 field와 RuleSet/Boundary
- Daon2.5 이후 선택적 graph projection

검색 결과는 reranker와 evidence gate를 통과한 뒤 LLM 또는 실행 파이프라인으로 전달한다.

### 9.2 근거 계약

모든 답변과 산출물의 근거는 최소 다음을 포함한다.

- source_id, source_version_id, source_type
- document_id, document_version_id
- page, region, block 또는 row/cell 위치
- 인용 텍스트와 evidence span
- 검색 점수와 rerank 순위(운영 메타데이터)
- 공용 지식이면 package/version, 전문가 지식이면 ruleset/version

### 9.3 부족한 근거 처리

- 근거가 없거나 충돌하면 답변을 사실처럼 확정하지 않는다.
- `근거 부족`, `출처 충돌`, `검토 필요`를 결과와 상태에 표시한다.
- 사용자가 근거 범위를 넓히거나 자료를 추가하거나 전문가 검토를 요청할 수 있게 한다.

## 10. 대화·실행 설계

### 10.1 요청 유형

- 질문 답변: `question_answer`
- 자료 요약·비교: `question_answer` 또는 업무 패키지의 명시된 분석 pipeline
- 구조화 추출: `knowledge_extract`
- 제약·준수 점검: `constraint_check`
- 보고서·문서 초안 생성: `result_generate`
- 결과 자체 검토: `engine_review`
- 사용자·검토자 피드백 반영: `feedback_apply`

문서 등록 후 이해가 필요한 경우 `document_understand`를 선행한다.

### 10.2 대화 원칙

- 대화는 workspace와 knowledge scope snapshot에 귀속된다.
- 답변에는 사용한 출처와 실행 상태를 함께 표시한다.
- 긴 실행은 accepted 응답 후 상태를 갱신하고 중복 제출을 idempotency key로 차단한다.
- 실행 오류는 raw stack trace가 아니라 안전한 오류 코드, 사용자 설명, 재시도 가능 여부로 표시한다.
- 일반 질문과 문서 생성은 같은 대화 안에서 가능하지만, 생성된 산출물은 StudioOutput으로 별도 버전 관리한다.

## 11. 업무 스튜디오 설계

### 11.1 Daon2 핵심 산출물

| 산출물 유형 | 사용자 가치 | 필수 구성 |
| --- | --- | --- |
| 근거 기반 보고서 | 여러 자료의 핵심 사실과 결론을 업무 문서로 정리 | 요약, 본문, 근거, 경고, 미확인 사항 |
| 제약 점검표 | 규정·약관·요구사항 충족 여부 확인 | 항목, 판정, 근거, 적용 RuleSet, 조치 |
| 비교·데이터 표 | 문서·조건·수치의 차이를 표로 비교 | 비교 기준, 값, 출처, 불일치·누락 |
| 지식 구조도 | 주요 개체·관계·조건을 이해 | node/edge, 근거, 신뢰도, 범위 |
| 업무 문서 초안 | 보고서·제안서·검토서 등 새 문서 작성 | 템플릿, 섹션, 근거, 검토 상태 |

### 11.2 산출물 공통 계약

모든 StudioOutput은 다음을 보존한다.

- output_id, output_type, workspace_id, owner_user_id
- title, summary, content_ref, format
- source scope snapshot과 evidence refs
- applied RuleSet/Boundary/package versions
- orchestration run, model/provider/prompt/tool lineage
- warnings, unresolved items, confidence summary
- output version, previous version과 변경 사유
- draft, review_requested, revision_requested, approved, delivered 상태
- 생성자, 검토자, 수신자와 시각

### 11.3 생성과 편집

- 생성 요청은 대화에서 시작할 수 있지만 결과는 업무 스튜디오에 독립 항목으로 저장한다.
- 사용자의 직접 편집과 AI 재생성은 서로 다른 revision으로 기록한다.
- 일부 섹션만 재생성할 때도 해당 섹션의 근거와 실행 trace를 보존한다.
- 외부 전달 전 검토가 필요한 업무 패키지는 승인 없이 delivered로 전환할 수 없다.
- 다운로드 파일에는 허용된 범위에서 근거 목록, 버전과 생성 시각을 포함한다.

### 11.4 제품 버전별 확장

| 제품 버전 | 스튜디오 범위 |
| --- | --- |
| Daon2 | 보고서, 제약 점검표, 비교·데이터 표, 지식 구조도, 업무 문서 초안 |
| Daon2.5 | 슬라이드, 인포그래픽, 지식 카드, 퀴즈, 템플릿, 공유·협업, 보험증권과 개인 맥락 |
| Daon3 | 오디오·동영상 브리핑, 상호작용형 산출물, 승인된 agent workflow와 다중 업무 협업 |

## 12. 핵심 사용자 흐름

### 12.1 새 워크스페이스와 개인 자료 분석

1. 사용자가 프로젝트·업무 패키지를 선택하고 워크스페이스를 만든다.
2. 파일을 추가하고 접근 범위와 보존 정책을 확인한다.
3. 시스템이 업로드, Vision-first 이해, 청킹, 임베딩, 검색 준비 상태를 자동 갱신한다.
4. 사용자는 실패·fallback·확인 필요 페이지를 화면에서 확인한다.
5. 사용 가능한 자료만 질문과 생성 범위에 포함된다.

이 흐름은 관리자 공용 RAG가 없어도 독립적으로 동작한다.

### 12.2 개인 자료와 승인 지식 결합

1. 사용자가 지식 범위를 연다.
2. 개인 자료, 프로젝트 승인 문서 지식, 전문가 지식과 RuleSet을 선택한다.
3. 시스템은 권한과 활성 버전을 확인하고 knowledge scope snapshot을 만든다.
4. 실행 결과는 선택한 범위와 버전을 항상 표시한다.

### 12.3 질문·점검·문서 생성

1. 사용자가 질문 또는 작업 유형을 선택한다.
2. 시스템이 의도와 필요한 pipeline을 결정하고 실행 계획을 표시한다.
3. 검색·RuleSet 평가·생성·검토가 실행된다.
4. 답변과 결과에 근거, 규칙, 경고, 상태가 함께 표시된다.
5. 문서 결과는 업무 스튜디오에 저장되고 후속 편집·검토·전달로 이어진다.

### 12.4 오류와 복구

- 소스 처리 실패: 페이지·블록 단위 재처리 또는 자료 제외
- Provider 장애: degraded, timeout, policy_blocked를 사용자 문구로 표시
- 근거 부족: 자료 추가 또는 범위 확대 제안
- 실행 실패: 멱등 재시도 가능 여부와 운영자 지원 식별자 표시
- 권한 변경: 과거 결과는 보존하되 원문 접근과 재실행을 현재 권한으로 재검증

## 13. 애플리케이션 구조

### 13.1 배포 흐름

    사용자 브라우저
      ↓ same-origin
    User Web / User BFF
      ├─ Session·RBAC
      ├─ Workspace API
      ├─ Source API
      ├─ Conversation·Run API
      └─ Studio Output API
      ↓ server-side
    Daon Platform
      ├─ projects / work_packages
      ├─ document_registry / document_understanding
      ├─ workflow_runtime / engine_gateway
      ├─ knowledge_assets / rulesets
      ├─ review_center / delivery
      └─ audit / notifications
      ↓ standard engine API
    daon_runtime
      ├─ GPU/CPU workers
      └─ external Provider adapters
      ↓
    PostgreSQL + pgvector + Object Storage + Evidence Store

### 13.2 기존 모듈 재사용

| 기존 모듈 | 사용자형 프로그램에서의 사용 |
| --- | --- |
| auth/rbac/users | 로그인, 세션, 워크스페이스 접근 권한 |
| projects/work_packages | 업무 문맥과 정책 선택 |
| document_registry | 개인 자료의 원본·파일·버전 |
| document_understanding | Vision-first 상태, 의미 블록, 근거 |
| workflow_runtime | 질문·점검·생성 run과 단계 |
| engine_gateway | daon_runtime 표준 호출 |
| knowledge_assets/rulesets | 승인된 공용·전문가 지식 참조 |
| review_center | 사용자 결과의 검토 요청과 수정 |
| delivery | 승인 결과의 사용자·외부 전달 |
| audit/notifications | 추적, 알림, 오류·완료 안내 |

### 13.3 사용자 제품 전용 경계

다음 모듈은 사용자 제품 전용으로 분리한다.

- platform/user_workspaces: 워크스페이스와 멤버·권한
- platform/workspace_sources: source refs, 버전, 처리 가능 상태
- platform/knowledge_scopes: 실행 범위와 immutable snapshot
- platform/conversations: 대화와 메시지·인용
- platform/studio_outputs: 산출물, 버전, 근거, 검토·전달 연결
- apps/api/src/modules/user_workspaces: 사용자용 API/BFF 조립
- apps/user-web: 브라우저 UI와 same-origin client

이 모듈들은 document, run, knowledge와 delivery 정본을 복제하지 않고 ID와 version으로 참조한다.

## 14. 데이터 모델

### 14.1 신규 정본 후보

| 테이블 | 책임 | 주요 연결 |
| --- | --- | --- |
| user_workspaces | 독립 업무 공간 | owner, project, work_package |
| workspace_members | 멤버와 권한 | workspace, user, role |
| workspace_source_refs | 개인·승인 지식 source 참조 | workspace, document/version 또는 package/version |
| workspace_index_versions | 개인 자료 검색 인덱스 버전 | workspace, source snapshot, build run |
| knowledge_scope_snapshots | 실행 시점 지식 범위 | workspace, run, source/package/ruleset versions |
| conversations | 대화 단위 | workspace, owner |
| conversation_messages | 사용자·assistant·system 결과 | conversation, run |
| message_evidence_refs | 답변 인용 | message, evidence span |
| studio_outputs | 산출물 정본과 현재 버전 | workspace, output type, current version |
| studio_output_versions | 생성·편집 revision | output, run, content ref |
| output_evidence_refs | 산출물 근거 | output version, evidence span |

### 14.2 기존 정본 참조

- 문서 원본과 버전은 documents, document_files, document_versions를 사용한다.
- 이해 결과는 document_understanding_runs, semantic_blocks, evidence_spans를 사용한다.
- 실행은 orchestration_runs, processing_jobs, job_steps를 사용한다.
- 승인 지식과 RuleSet은 knowledge asset 계층의 발행 버전을 참조한다.
- 검토·결정·전달·감사는 기존 review, delivery, audit 정본을 사용한다.

### 14.3 삭제와 보존

- 워크스페이스 삭제는 즉시 물리 삭제가 아니라 비활성화와 보존 기간을 거친다.
- 공용 지식 참조가 삭제되어도 과거 실행 snapshot과 감사 계보는 보존한다.
- 개인 자료의 원문 삭제 시 파생 인덱스·미리보기·cache를 비동기 정리하고 완료 상태를 기록한다.

## 15. same-origin BFF API 목표

브라우저 공개 경로는 모두 사용자 웹 origin 기준 상대 경로다.

| Method | 경로 | 목적 |
| --- | --- | --- |
| GET | /api/user/session | 로그인·권한·프로젝트 범위 |
| GET/POST | /api/user/workspaces | 목록·생성 |
| GET/PATCH | /api/user/workspaces/{workspace_id} | 상세·설정 |
| GET/POST | /api/user/workspaces/{workspace_id}/sources | 자료 목록·등록 |
| GET | /api/user/workspaces/{workspace_id}/sources/{source_id} | 처리·근거 상태 |
| GET/PUT | /api/user/workspaces/{workspace_id}/knowledge-scope | 범위 조회·저장 |
| GET/POST | /api/user/workspaces/{workspace_id}/conversations | 대화 목록·생성 |
| GET/POST | /api/user/conversations/{conversation_id}/messages | 대화 조회·요청 |
| GET | /api/user/runs/{run_id} | 실행 상태·단계 |
| GET/POST | /api/user/workspaces/{workspace_id}/studio-outputs | 산출물 목록·생성 |
| GET/PATCH | /api/user/studio-outputs/{output_id} | 상세·편집·버전 |
| POST | /api/user/studio-outputs/{output_id}/review-requests | 검토 요청 |
| GET | /api/user/deliveries | 전달 결과 |

원칙:

- BFF는 세션에서 user, project, work package와 trace를 결정한다.
- 클라이언트가 actor, 내부 worker, provider/model, 내부 URL을 request body로 지정하지 않는다.
- 내부 API 주소와 secret은 server-side 설정·Secret Store에서만 사용한다.
- URL 소스 fetch도 브라우저가 직접 수행하지 않고 server-side 안전 fetch adapter를 사용한다.
- 모든 write는 idempotency와 권한·소유권을 확인한다.

## 16. 상태와 안전 오류

### 16.1 화면 상태 그룹

| 그룹 | 화면 상태 |
| --- | --- |
| 자료 | 등록 중, 분석 중, 검색 준비 중, 사용 가능, 확인 필요, 실패 |
| 실행 | 접수, 대기, 처리 중, 검토 필요, 완료, 실패, 취소 |
| 산출물 | 생성 중, 초안, 검토 요청, 수정 요청, 승인, 전달, 실패 |
| 지식 범위 | 활성, 새 버전 있음, 권한 만료, 비활성, 충돌 |

### 16.2 안전 오류 예시

- workspace_not_found
- workspace_access_denied
- source_not_ready
- source_access_expired
- knowledge_scope_invalid
- evidence_insufficient
- run_already_active
- provider_temporarily_unavailable
- review_required
- delivery_not_allowed

raw SQL, 내부 host, stack trace, API Key 이름과 Provider 응답 원문은 브라우저 오류에 포함하지 않는다.

## 17. 보안·개인정보·거버넌스

1. 워크스페이스, source, 대화, 산출물마다 owner와 접근 범위를 저장한다.
2. 프로젝트·업무 패키지 권한을 모든 read/write/run에서 다시 확인한다.
3. 업로드 파일은 MIME, 확장자, 크기, 악성 파일과 암호화 여부를 검사한다.
4. URL·connector 소스는 SSRF, redirect, private network, credential leakage를 차단한다.
5. 개인 자료와 공용 지식은 인덱스 namespace와 retrieval filter를 분리한다.
6. 워크스페이스 공유·다운로드·전달은 audit event를 남긴다.
7. 보존 기간, 삭제 요청, legal hold와 export 정책을 분리한다.
8. 비밀값은 브라우저, HTML, NEXT_PUBLIC 설정과 DB 평문 metadata에 저장하지 않는다.
9. 외부 Provider 전송 시 허용된 자료 범위, masking과 region 정책을 적용한다.
10. 생성 결과에는 AI 생성 여부, 근거 범위, 미확인 사항을 표시한다.

## 18. 독립 병렬 개발 트랙

기존 DEV-032~044의 플랫폼·엔진·공용 기능 구현과 다음 사용자 트랙을 병렬로 진행한다.

| 단계 | 작업 | 의존성 | 실제 확인 지점 |
| --- | --- | --- | --- |
| DEV-U01 | 독립 제품 shell과 3면 워크스페이스 actual UI | 공통 shared-ui | 실제 process·Chrome navigation·responsive |
| DEV-U02 | workspace/source/scope 데이터·권한 계약 | auth, projects, work_packages, document_registry | PostgreSQL 계약·권한 테스트 |
| DEV-U03 | 사용자 same-origin BFF와 workspace CRUD | U02, 세션 adapter | 실제 BFF process·HTTP·인증 |
| DEV-U04 | 파일 자료 등록·Vision-first 상태·근거 연결 | DEV-034, DEV-043, U03 | 실제 파일·DB·worker·화면 |
| DEV-U05 | grounded 대화·검색 범위·인용 연결 | DEV-036, U04 | 실제 질문·근거·Network |
| DEV-U06 | Daon2 핵심 업무 스튜디오 산출물 | DEV-037, U05 | 보고서·점검표·표·구조도·초안 |
| DEV-U07 | 산출물 버전·검토·다운로드·전달 | DEV-038~040, U06 | review/delivery/audit E2E |
| DEV-U08 | 관리자 문서 RAG·전문가 RAG/RuleSet 결합 | DEV-039, U03 | 지식 범위 snapshot과 권한 |
| DEV-U09 | 사용자 제품 독립 all-process E2E | U04~U08, DEV-042~044 | 로그인부터 전달까지 실제 Chrome |

DEV-U01~U03은 관리자·전문가 지식 발행 기능과 무관하게 즉시 시작할 수 있다. DEV-U04 이후는 실제 object storage, document understanding, Provider와 인증 adapter의 준비 시점에 맞춰 연결한다.

## 19. Daon2·2.5·3.0 연계

### 19.1 Daon2 2.0

- 파일 중심 개인 워크스페이스
- 승인된 문서 지식·전문가 RuleSet 참조 계약
- Vision-first 이해 상태와 근거 뷰어
- 질문, 요약, 비교, 제약 점검, 문서 초안
- 보고서·점검표·데이터 표·지식 구조도
- 버전, 검토, 다운로드, 전달과 감사
- same-origin BFF와 실제 process/browser E2E

### 19.2 Daon2.5

- URL snapshot과 조직 connector 단계 도입
- 보험증권과 개인 업무 맥락 결합
- 전문가 지식 RAG와 Knowledge Package 고도화
- 슬라이드·인포그래픽·지식 카드·퀴즈
- 템플릿, 공유, 협업, 댓글과 공동 검토
- 선택적 graph preview와 Actor-Critic 준비

### 19.3 Daon3.0

- 오디오·동영상·live source와 업무 시스템 이벤트
- 상호작용형 산출물과 지식 맵
- 승인된 멀티 에이전트 업무 실행
- Decision Trace와 전략 선택
- Graph projection, MCP 도구와 다중 업무 협업
- 검증된 업무에 한한 제한적 최적화 전략

## 20. 완료 기준과 미확정 항목

### 20.1 Daon2 사용자 제품 완료 기준

- 사용자가 Python, DB와 CLI 없이 워크스페이스를 만들고 자료를 등록한다.
- 실제 파일이 Object Storage·PostgreSQL·Vision-first worker를 거쳐 사용 가능 상태가 된다.
- 개인 자료와 승인 지식 범위를 선택해 근거 포함 질문·점검·생성을 수행한다.
- 다섯 종류 핵심 StudioOutput을 생성·버전·검토·다운로드·전달한다.
- 브라우저 Network에 localhost, 내부 Docker host, Provider URL, API Key가 없다.
- 권한 없는 workspace/source/output 접근이 HTTP 401/403/404 안전 계약으로 차단된다.
- 실패·재처리·근거 부족·검토 필요 상태가 화면에서 확인된다.
- 실제 process 재기동, cleanup, DB baseline과 감사 계보를 검증한다.
- 관리자·전문가 앱이 중지되어도 개인 자료 전용 워크스페이스의 허용된 기능은 동작한다.

### 20.2 후속 확정이 필요한 운영 정책

- 워크스페이스별 저장 용량·토큰·실행 비용 한도
- URL·connector 도입 순서와 보안 allowlist
- 개인 자료의 기본 보존 기간과 조직 legal hold
- 실시간 공동 편집의 도입 버전
- 산출물별 Word/PDF/PPTX export 템플릿
- 모바일 전용 앱 필요 여부

이 항목은 구현 중 임시 상수로 고정하지 않고 관리자 정책·업무 패키지 설정 또는 후속 설계 결정으로 관리한다.

## 21. 최종 설계 결론

사용자형 프로그램은 관리자·전문가 프로그램과 분리해 지금부터 병렬 개발한다. 독립성의 기준은 별도 기술 스택이나 별도 데이터 섬이 아니라, 사용자 워크스페이스·개인 자료·대화·산출물의 명확한 소유권과 표준 계약이다.

Daon2는 개인 자료 기반의 근거 질문·점검·문서 생성과 핵심 업무 스튜디오를 완성한다. Daon2.5는 전문가 지식·개인 맥락·협업 산출물을 확장하고, Daon3는 멀티미디어·상호작용·에이전트형 실행으로 확장한다. 세 버전은 Vision-first, evidence, approval, version, audit, same-origin BFF라는 동일한 불변 계약을 계승한다.

# R1-M6-10-C01 수정 작업지시서 — CP3 실제 Web Thin Vertical E2E

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-10-C01` |
| Issue ID | `R1-M6-10-I001` |
| 상태 | `READY` · CP3 Go 실행 |
| 설계 근거 | 상세 설계서 §8.2, §9~10, §15.1, §16~18, §23.1, §24 |
| 계획 근거 | Release 1 계획 1.4의 R1-M6-01·02·05·06·08·09·10 및 CP3 |
| 승인 근거 | `APR-CP3-GO-20260804-01` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-10-C01_progress.md` |

## 판정과 목표

기존 R1-M6-10의 내부 Run 계약은 합격이나, 실제 Process·PostgreSQL·Object Storage·LLM·Production Web을 통합한 CP3 여정은 미완료다. 본 수정 작업은 사용자가 화면에서 Provider·역할별 Model을 선택하고, 단일 PDF를 등록해 질문과 인용 원문 열기까지 실제로 완료하는 CP3를 닫는다.

## 사용자 관점 완료 조건

1. 로그인한 사용자가 설정 화면에서 `CEREBRAS`, `GROQ`, `MISTRAL`, `OPENAI`, `UPSTAGE`, `GEMINI`, `OPENROUTER`, `ANTHROPIC`, `OLLAMA` Provider Profile을 조회·편집할 수 있다.
2. Provider base URL, 모델 ID, 역할 매핑, 활성·선택 상태는 DB에 저장하고 화면에서 관리한다. API Key·Secret은 서버 `.env`에서만 읽고 API 응답·HTML·Browser Network·로그·DB에 노출하지 않는다.
3. CP3에서는 화면에서 선택한 하나의 승인 Provider 및 역할별 ModelDeployment를 고정하고 자동 Fallback을 금지한다. 실행 증거는 UI 선택값, Route, Provider Network, EgressDecision, ModelAttempt, Artifact/Deployment Digest가 일치해야 한다.
4. PDF 등록은 same-origin BFF를 통해 실제 Object Storage와 PostgreSQL 정본에 Source·SourceVersion·Digest를 생성한다.
5. 문서 처리는 Vision/LLM-first 의미·문맥 이해를 먼저 수행하고 Parser/OCR은 검증·보완만 한다. Parser-only 성공을 `ready`로 판정하지 않는다.
6. 의미 Chunk를 색인한 뒤 사용자 질문에 실제 LLM 응답과 PDF Page Citation을 반환하고, Citation을 누르면 당시 SourceVersion의 Page·문맥을 열어야 한다.
7. Run은 `accepted→planning→retrieving→generating→validating→completed` 이력과 SourceVersion·UnderstandingResult·ModelAttempt·Citation·Audit를 동일 Trace/Run으로 보존한다.
8. ysna-server의 Production-like Docker와 Production Chrome에서 실제 여정을 재실행하고 Mock 0건, Browser Console error 0건, 내부 API 주소 노출 0건을 증명한다.

## 구현 범위

- Provider Profile·ModelDeployment·Role Binding의 PostgreSQL Migration, Repository, 관리 API, 설정 UI
- 현재 서버 `.env`의 Provider Key 존재 여부만 안전하게 연결하는 Credential Resolver; Key 값은 절대 반환하지 않음
- 선택된 승인 Provider의 실제 Adapter와 Health/Readiness. CP3 실행 Provider는 신산님이 선택한 Upstage를 우선 사용하되 구조는 Provider 독립을 유지
- 단일 PDF upload·보안 검사·Object/PostgreSQL 저장·비동기 Processing Run
- Vision/LLM 의미 이해, Parser/OCR 검증·보완, 증거 reconciliation, Index/Retrieval, 질문, Citation Viewer
- Web same-origin BFF와 Workspace 3면 UI의 실제 API 연결
- 표준 오류, 401/403/404 비노출, 클라이언트 timeout·Provider 실패·`waiting_model` 표시

## 제외 범위

- 단일 PDF 외 추가 형식, 인터넷·Daon·RuleSet Connector, 자동 Fallback, Local LLM, 모바일·Windows 여정의 추가 확장
- `.env`에 Provider URL·모델 ID·역할 매핑 추가
- Browser에서 Provider/API 절대주소, localhost, Docker Host/Port 직접 호출
- 기존 DB·Object·다른 서비스의 삭제·초기화·교체

## TDD·안전 순서

1. 기존 정보 유출·Provider 자동 Fallback·Parser-only ready·Tenant 교차 접근·same-origin 우회를 재현하는 RED 테스트를 먼저 추가한다.
2. 현재 `master` 및 기존 DB Migration을 보존하는 추가 Migration만 사용한다.
3. 서버 Adapter·API·BFF·UI 순으로 GREEN을 만들고 단계별 회귀를 수행한다.
4. 모든 명령·오류·복구·테스트·다음 지점을 진행 기록에 즉시 추가한다.
5. 배포는 로컬 회귀 통과 후 `master` push→ysna-server pull→Migration preflight/backup/apply→API/Web 재빌드 순서로만 한다.
6. 기존 정상 기능과 무관 파일을 변경하지 않고 신산님의 untracked 문서를 stage하지 않는다.

## 필수 검증 증거

- 전용 Unit·Integration·Contract·Security·Migration·Web Build 통과
- PostgreSQL 재적용·RLS/Tenant 격리·Object Digest 일치
- Provider Key 비노출 재귀와 선택한 ModelDeployment·Network·Egress·Attempt 일치
- 실제 단일 PDF의 등록→이해→검증→색인→질문→Citation 원문 열기
- Production Chrome Network·Console·화면 Screenshot, API·DB·Object·Audit 대조
- `docs/04_test_reports/release_1/evidence/R1-M6-10-C01/` Evidence Manifest

## 결과 보고 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`을 모두 포함하고 `판정 → 판단 이유 → 조치`로 보고한다. 정적 코드·Mock·HTTP 200만으로 CP3를 통과했다고 보고하지 않는다.

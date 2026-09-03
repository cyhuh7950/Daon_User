# Daon 지식·LLM 활용 가이드

- Release: 1.0.0
- 문서 업데이트: 2026-09-03
- 언어: 한국어(ko-KR)
- 대상: 지식 관리자, 업무 사용자, 검토자, Workspace 관리자, LLM 운영 담당자
- 목표: Source, Daon 승인 지식, 모델, Citation과 외부 전송을 현재 구현 범위 안에서 안전하게 사용합니다.

> 이 가이드는 설계상 목표와 현재 실제 서비스 경로를 구분합니다. UI에 보이는 권위나 Provider 설정만으로 자동 정책 적용과 실행 성공을 가정하지 마세요.

## 1. 목적과 핵심 원칙

- 질문에 사용할 Source를 사용자가 명시적으로 선택합니다.
- 근거 질문은 Citation을 원문과 함께 검토합니다.
- Provider 연결과 실제 질문 실행 지원을 구분합니다.
- 외부 전송은 조직 정책을 통과한 경우에만 수행합니다.
- 모델과 설정을 자동으로 임의 변경하지 않습니다.
- 미지원 형식이나 기능은 성공을 가장하지 않고 안전하게 중단합니다.
- Version, Run과 산출물의 계보를 보존합니다.

## 2. 용어

- Raw Source: 사용자가 직접 등록한 파일이나 텍스트
- Daon 승인 지식: Daon 2, Daon 2.5, Daon 3에서 생산·등록된 지식 패키지
- 연결형 Source: MCP나 Connector를 통해 참조하는 외부 Source
- Source Version: 특정 시점 Source의 변경 불가능한 Version
- Evidence: 질문 처리에 실제 사용된 근거 조각
- Citation: 답변에서 Evidence 위치를 가리키는 참조
- Provider Profile: LLM 제공자 연결 설정
- Model Deployment: Provider의 특정 모델과 역할 설정
- Run: 한 번의 질문 또는 생성 실행
- RunSnapshot: 실행 시점의 선택과 정책 기록
- RuleSet: 결과에 적용할 규칙 집합
- Egress: 데이터를 외부 Provider로 전송하는 행위

## 3. 접근 경로와 Source 유형·현재 지원 수준

공개 범위에서는 지식·LLM의 안전 원칙과 설명서를 확인합니다. 실제 Source, 승인 지식, Provider, 모델, Citation과 조직 정책은 로그인 후 조직 전용 범위입니다.

### 3.1 파일 선택기 등록 형식

Web 화면은 다음 확장자를 선택할 수 있습니다.

- 문서: PDF, DOCX, PPTX
- 표: XLSX, CSV
- 텍스트: TXT, Markdown
- 이미지: PNG, JPG, JPEG
- 오디오: M4A, WAV, MP3

이 목록은 업로드 입력 계약입니다. 모든 형식에 대해 의미 이해, 근거 추출, Citation 렌더링과 실제 질문이 완료되었다는 의미가 아닙니다.

### 3.2 현재 권장 형식

운영 질문과 페이지 Citation을 함께 확인하려면 PDF를 사용합니다. 현재 실제 운영 의미 이해 경로에서 확인된 중심 형식입니다.

PDF 외 형식은 다음 조건을 모두 확인한 경우에만 업무 근거로 사용합니다.

1. Source가 `사용 가능` 상태입니다.
2. 해당 형식의 Adapter가 실제 환경에 연결되어 있습니다.
3. 선택 모델이 해당 입력 역할을 지원합니다.
4. Citation 또는 검토 가능한 근거 위치가 제공됩니다.
5. 조직이 해당 형식의 실제 QA를 완료했습니다.

### 3.3 Windows 로컬 Import

Windows 로컬 경로에는 PDF, plain text와 Markdown Import 계약이 있습니다. 로컬에서 등록되었다는 사실은 Cloud 질문이나 동기화 완료를 뜻하지 않습니다. Cloud로 옮길 때는 Preview, 승인과 Version 충돌 확인이 필요합니다.

### 3.4 복사한 텍스트

Web의 `복사한 텍스트`에서 직접 등록할 수 있습니다. 원문 위치가 파일 페이지처럼 고정되지 않으므로 Citation과 Version을 더 주의해서 검토합니다.

### 3.5 웹사이트·Drive·MCP

- 웹사이트와 Drive는 Connector가 연결되어야 합니다.
- 국가법령정보센터 MCP 등록 흐름이 제공될 수 있습니다.
- Connector가 `사용 불가`이면 재연결 상태를 먼저 확인합니다.
- Connector 삭제는 외부 원본 데이터 삭제가 아닙니다.

## 4. Source 처리와 Ready 판정

대표 처리 흐름은 `등록됨 → 보안 확인 중 → 처리 중 → 색인 중 → 사용 가능`입니다.

다음 상태는 완료가 아닙니다.

- `모델 대기`: 처리에 필요한 모델이 없습니다.
- `부분 이해`: 일부만 이해되어 사람이 확인해야 합니다.
- `검토 필요`: 품질이나 근거 검토가 필요합니다.
- `처리 실패`: Run이 실패했습니다.
- `만료됨`: 현재 Version을 사용할 수 없습니다.
- `사용 중지`: 정책 또는 관리 상태로 비활성화되었습니다.

처리 실패 후 원본을 삭제할 필요는 없습니다. 형식, 모델, Queue와 정책을 확인한 뒤 새 처리 Run을 시작하는 방식으로 복구합니다.

## 5. 조작: Daon 승인 지식 사용

### 5.1 화면에서 확인할 항목

승인 지식 목록에서 다음 정보를 확인합니다.

- 생산자: Daon 2, Daon 2.5, Daon 3
- 생산자 Version
- 권위 Label
- 등록 상태
- 선택 여부

### 5.2 질문에 선택하기

1. `Daon 승인 지식` 목록에서 패키지를 선택합니다.
2. 가운데 질문 영역이 근거 질문 상태로 바뀌는지 확인합니다.
3. 질문을 실행합니다.
4. Citation origin이 Daon 지식인지 확인합니다.

### 5.3 현재 권위·가중치·충돌 처리 제한

설계에는 Daon 승인 지식 우선순위, 사용자 가중치, 중요 충돌 검출과 강제 RuleSet이 정의되어 있습니다. 관련 UI와 `knowledge_retrieval`, `ruleset_connector` 같은 별도 모듈도 존재합니다.

그러나 현재 실제 질문 서비스 경로는 이 모듈을 호출해 권위·가중치·충돌·강제 RuleSet을 일관되게 적용하는 것으로 확인되지 않았습니다. 따라서 다음과 같이 사용합니다.

- 권위 Label을 자동 정답 판정으로 사용하지 않습니다.
- 상충하는 Source가 있으면 각 Citation 원문을 따로 검토합니다.
- 중요 업무는 검토자와 승인자의 확인을 거칩니다.
- 강제 RuleSet이 적용되었다는 별도 실행 증거가 없으면 적용된 것으로 기록하지 않습니다.
- Daon 지식이 없을 때 Raw Source로 자동 전환되었다고 가정하지 않습니다.

## 6. 질문 Context 구성

### 6.1 Raw Source 사용

Raw Source를 선택하면 질문 요청에 Source ID와 Source Version이 포함됩니다. 답변 Citation은 선택한 Source와 Version 범위 안에 있어야 합니다.

### 6.2 Daon 지식 사용

승인 지식을 선택하면 질문 Context에 지식 패키지 식별자가 포함됩니다. Citation은 `daon_knowledge` origin으로 구분되어야 합니다.

### 6.3 Context 없이 질문

Source와 승인 지식을 선택하지 않으면 `작업 상담 · 근거 미사용` 상태입니다. 허용 범위는 인사, 감사, 도움말과 Daon 사용법 같은 제한된 일반 대화입니다. 업무 사실 질문의 우회 경로로 사용하지 않습니다.

## 7. Provider와 모델

### 7.1 설정 화면에 등록된 Provider

`LLM 설정`에서 다음 Provider Profile을 구성할 수 있습니다.

- Cerebras
- Groq
- Mistral
- OpenAI
- Upstage
- Gemini
- OpenRouter
- Anthropic
- Ollama

Profile에는 Provider 종류, 안전한 Base URL, 활성 상태와 Credential 설정 여부가 포함됩니다. Credential 원문은 화면에 다시 표시하지 않습니다.

### 7.2 실제 질문 실행 지원 Provider

현재 질문·일반 대화 Adapter가 직접 실행하는 Provider는 다음 네 종류입니다.

- Ollama
- Groq
- Mistral
- Upstage

OpenAI, Cerebras, Gemini, OpenRouter와 Anthropic은 설정 목록에 존재하더라도 현재 질문 Adapter에서 `사용 불가`가 될 수 있습니다. 연결 시험 가능 여부와 실제 text 생성 지원 여부를 분리해서 기록합니다.

### 7.3 모델 역할

Deployment에는 다음 역할을 지정할 수 있습니다.

- text
- vision
- document_parser
- audio_understanding
- speech_to_text
- embedding
- reranker

질문에는 text 역할의 활성 모델이 필요합니다. 파일 처리에는 형식에 따라 다른 역할이 필요할 수 있습니다. 역할 Label만 등록하고 실제 기능 시험을 하지 않은 모델은 사용 가능한 것으로 판정하지 않습니다.

### 7.4 Ollama

Ollama는 서버 또는 승인된 로컬 환경에 설치된 모델을 사용합니다. Daon이 모델을 자동 설치하거나 삭제하지 않습니다. 모델 이름, 역할, 활성 상태와 Endpoint가 정확한지 확인합니다.

## 8. 모델 선택과 Routing의 현재 동작

질문 실행은 현재 Workspace에서 선택된 단일 Deployment를 사용합니다. 실행 시 선택, Provider와 정책 정보가 RoutingDecision과 RunSnapshot에 기록될 수 있습니다.

현재 서버가 설계서의 다중 후보 우선순위를 계산해 자동 정렬하고, 장애 시 다음 모델로 자동 Fallback한다고 설명할 수는 없습니다. 실제 기록의 후보 순서는 선택된 Deployment 한 개를 중심으로 구성됩니다.

따라서 다음 원칙을 지킵니다.

- 실행 전 사용할 모델을 명시적으로 확인합니다.
- 실패한 Run에서 모델이 자동 교체되었다고 가정하지 않습니다.
- 다른 모델을 사용하려면 설정을 확인한 뒤 새 Run으로 실행합니다.
- Provider 장애를 숨기기 위해 임의 Fallback을 만들지 않습니다.
- 모델 변경 전후 결과는 서로 다른 Run으로 비교합니다.

## 9. 외부 전송 정책

### 9.1 정책 확인

`설정 → 조직 정책`에서 다음을 확인합니다.

- 외부 전송 차단 또는 승인된 외부 전송
- 허용 Provider 종류
- 허용 목적지
- 데이터 분류
- 최대 전송량
- 마스킹과 삭제 처리 필요 여부
- 필수 승인자

### 9.2 실행 전 확인

1. 질문에 포함할 Source를 확인합니다.
2. 선택 Provider가 외부 Provider인지 확인합니다.
3. 조직 정책이 Provider와 목적지를 허용하는지 확인합니다.
4. 데이터 분류와 최대 전송량을 확인합니다.
5. 필요한 마스킹과 삭제 처리가 적용되었는지 확인합니다.
6. 추가 승인이 필요하면 Step-up 절차를 수행합니다.

정책이 거부한 요청은 Endpoint 변경, 다른 Provider 선택 또는 내부 주소 직접 호출로 우회하지 않습니다.

## 10. 질문 실행과 Citation

### 10.1 질문 실행

1. 준비된 Source 또는 승인 지식을 선택합니다.
2. text 모델 선택 상태를 확인합니다.
3. 외부 전송 정책을 확인합니다.
4. 구체적인 질문을 입력합니다.
5. 질문을 실행합니다.
6. 답변 상태와 Citation을 검토합니다.

### 10.2 Citation 필수 검토 항목

- origin이 Raw Source인지 Daon 지식인지
- Source Version 또는 지식 Context가 현재 선택과 일치하는지
- PDF 페이지 또는 locator가 유효한지
- 원문 내용이 답변을 실제로 뒷받침하는지
- 질문에 선택하지 않은 Context가 섞이지 않았는지

### 10.3 Citation이 부족할 때

- 답변을 승인하거나 외부에 전달하지 않습니다.
- Source 선택을 다시 확인합니다.
- 질문 범위를 좁힙니다.
- 처리 상태와 Evidence 준비 상태를 확인합니다.
- 다른 근거를 추가할 때는 새 Run으로 실행합니다.

## 11. Studio에서 지식과 모델 사용

Studio 생성은 Source Version, 목적, 독자, 언어, 분량, 구성, 출력 형식과 검토 조건을 Snapshot으로 묶습니다.

다음 산출물은 구조화된 생성 경로가 있습니다.

- 근거 기반 보고서
- 제약·준수 점검표
- 비교·데이터 표
- 지식 구조도
- 업무 문서 초안
- 슬라이드
- 인포그래픽
- 플래시카드
- 퀴즈

AI 오디오와 동영상은 계약에 등록되어 있지만 전용 Provider가 없으면 `STUDIO_OUTPUT_UNAVAILABLE`로 종료됩니다. 가짜 미디어 파일을 만들지 않습니다.

생성 후에는 내용뿐 아니라 Citation, Source Version, Run과 Output Version을 함께 확인합니다. 편집·재생성·설정 변경은 각각 새 Version을 만듭니다.

## 12. 품질·비용·지연 관리

### 12.1 품질

- 질문과 직접 관련된 Source만 선택합니다.
- `사용 가능` 상태의 Source를 사용합니다.
- 답변을 Citation 원문과 비교합니다.
- 상충 근거를 숨기지 않습니다.
- 중요한 결과는 검토와 승인을 거칩니다.

### 12.2 비용

- 불필요한 Source를 질문 Context에 넣지 않습니다.
- 필요한 결과 분량을 먼저 정합니다.
- 같은 질문을 여러 Provider에 무의미하게 반복하지 않습니다.
- 연결 시험과 실제 생성 비용을 구분합니다.

### 12.3 지연

- 처리 중인 Source가 끝나기 전에 질문을 반복하지 않습니다.
- 큰 문서는 질문 범위를 좁힙니다.
- Queue 상태가 주의이면 완료 시간을 확인합니다.
- 생성 지연 시 Library의 작업 상태를 다시 확인합니다.

### 12.4 재현성

- 질문, Source Version, 모델, 정책과 생성 설정을 기록합니다.
- 결과 비교는 Run 단위로 수행합니다.
- 기존 Output Version을 덮어쓰지 않습니다.
- 모델을 바꾼 경우 새 Run과 새 Version으로 남깁니다.

## 13. 예상 결과와 권장 운영 절차

1. 조직 관리자가 Provider Profile과 Credential을 설정합니다.
2. 역할에 맞는 Model Deployment를 등록하고 활성화합니다.
3. 연결 시험을 수행합니다.
4. 실제 지원 Provider로 작은 비민감 질문을 시험합니다.
5. 조직 Egress 정책과 실행 기록을 확인합니다.
6. PDF Source로 질문·Citation 수직 흐름을 검증합니다.
7. Studio 산출물과 Export를 검증합니다.
8. 실제 사용자 역할로 다시 확인합니다.

연결 시험 성공만으로 4단계 이후를 통과한 것으로 기록하지 않습니다.

## 14. 제한·오류 대응

### Provider 설정이 필요함

text 역할 Deployment, Profile 활성 상태, Credential 설정 여부와 선택 상태를 확인합니다. Credential 값을 화면이나 로그로 확인하려 하지 않습니다.

### TEXT_PROVIDER_UNAVAILABLE

선택 Provider가 현재 질문 Adapter 지원 범위인지 확인합니다. Ollama, Groq, Mistral, Upstage 외 Provider는 설정되어 있어도 질문 경로에서 사용할 수 없을 수 있습니다.

### TEXT_MODEL_NOT_SELECTED 또는 모델 선택 오류

Workspace의 text 역할 활성 Deployment를 선택합니다. 변경 후 기존 Run을 재사용하지 말고 새 Run을 실행합니다.

### EGRESS 정책 거부

조직 정책의 Provider, 목적지, 데이터 분류, 전송량, 마스킹과 승인자 조건을 확인합니다. 우회하지 않습니다.

### Source가 모델 대기 상태

필요한 document parser, vision, audio understanding, speech-to-text 또는 text 역할이 준비되었는지 확인합니다. Source 원본은 보존하고 준비 후 새 처리 Run을 시작합니다.

### QUESTION_RESPONSE_INVALID 또는 CITATION_RESPONSE_INVALID

결과를 사용하지 않습니다. 선택 Context와 Citation 계보를 다시 확인하고 새 Run으로 실행합니다.

### STUDIO_OUTPUT_UNAVAILABLE

선택 산출물 유형을 현재 Provider가 지원하지 않습니다. AI 오디오와 동영상은 전용 Provider가 준비될 때까지 다른 산출물로 가장하지 않습니다.

### 비용 한도 초과

질문 Context와 출력 분량을 줄이고 조직 한도를 확인합니다. 같은 실패 Run을 자동 재개하지 않습니다.

## 15. 관리자 점검표

- Provider 연결과 실제 질문 실행 지원을 구분했습니다.
- text 역할 Deployment가 명시적으로 선택되었습니다.
- Source 형식별 실제 처리 수준을 확인했습니다.
- 외부 전송 정책과 마스킹 조건을 확인했습니다.
- PDF 질문과 Citation을 실제로 열어 보았습니다.
- 모델 변경과 재실행을 새 Run으로 기록했습니다.
- Studio 결과의 Source Version과 Citation을 확인했습니다.
- 권위·가중치·RuleSet 자동 적용을 검증 없이 주장하지 않았습니다.
- 비밀번호, Credential, Token과 원문 데이터가 로그에 남지 않았습니다.

## 16. 현재 제한사항 요약

- 지식 권위·가중치·충돌·강제 RuleSet은 실제 질문 경로의 자동 강제로 검증되지 않았습니다.
- 다중 후보 모델 정렬과 자동 Fallback은 현재 실제 질문 서비스 기능으로 제공되지 않습니다.
- RoutingDecision과 RunSnapshot 저장이 있더라도 후보가 단일 선택 모델일 수 있습니다.
- PDF 외 등록 형식 전체의 운영 의미 이해는 미완료입니다.
- 설정 가능한 9개 Provider와 실제 질문 Adapter가 지원하는 4개 Provider는 다릅니다.
- AI 오디오·동영상은 전용 Provider가 없으면 사용할 수 없습니다.
- Mobile과 실제 기기에서의 지식·모델 흐름은 별도 검증이 필요합니다.

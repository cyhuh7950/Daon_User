# Daon 지식·LLM 활용 가이드

- Release: 1.0.0
- 언어: 한국어(ko-KR)
- 대상: 지식 관리자, 업무 사용자, 검토자, LLM 운영 담당자
- 범위: 공개 원칙과 로그인 후 조직 전용 설정을 구분합니다.

## 1. 목적

Daon2·2.5·3에서 생성·등록된 지식과 사용자가 추가한 Raw Source를 함께 활용하면서 Provider·Model, Citation, 품질·비용·지연을 안전하게 운영하는 기준을 제공합니다. Source 형식을 Daon이 임의로 제한하지 않고, 선택 LLM이 처리할 수 없는 경우 해당 Run만 fail-close하는 원칙을 설명합니다.

## 2. 접근 경로

### 공개 범위

문서 Hub의 `지식·LLM 활용 가이드`에서 이중 입력 원칙, Citation 검토법, 비용·지연 관리와 Safe error 대응을 읽을 수 있습니다.

### 로그인 후 조직 전용

- 지식 선택: `Source` 패널
- Provider·Model: `설정 → LLM 설정`
- 질문·Citation: 가운데 `대화·실행`
- 생성 결과·Version: 오른쪽 `업무 Studio`와 Library
- 강제 정책: `설정 → 조직 정책` 읽기 전용

Credential 원문, 내부 Endpoint, 조직 정책 fingerprint는 화면과 문서에 표시하지 않습니다.

## 3. 조작

### 3.1 입력 모드 선택

1. `Daon 지식 우선`: 승인·등록된 Daon 지식을 기본 근거로 사용합니다. 적격 Daon 지식이 없으면 Raw Source로 조용히 바꾸지 않습니다.
2. `혼합`: Daon 지식과 Raw Source를 함께 사용합니다. 상충 근거를 숨기지 않습니다.
3. `Raw Source만`: 사용자가 명시적으로 선택한 Source만 사용하고 `unverified_input`과 강화된 검토 조건을 유지합니다.

Daon 지식과 Raw Source는 모두 Text, 문서, 웹, 표, 이미지, 음성, 영상, DB/API Projection 등 지식 Source가 될 수 있습니다. 현재 Adapter가 처리할 수 없는 형식도 원본과 digest는 보존합니다.

### 3.2 Provider·Model 선택

1. `설정 → LLM 설정`을 엽니다.
2. Provider 카드에서 설정·Credential 여부·연결 상태를 확인합니다.
3. 현재 업무에 필요한 역할을 가진 Deployment·Model을 선택합니다.
4. 연결 시험은 Provider별 연결 계약만 확인합니다. 9개 Provider 모두에 같은 기능 시험을 반복하지 않습니다.
5. 생성 기능 시험은 승인 원칙에 따라 Upstage·Groq·Mistral 중 대표 하나를 선택하고, 호환성 의심 시에만 두 번째를 사용합니다.

![Provider 선택과 설정 상태](../../03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/foundation-b1-llm-settings-browser.png)

Offline Ollama는 이미 설치된 completion 모델의 exact name·digest·capability가 확인될 때만 후보로 표시합니다. Daon이 모델을 설치·삭제하지 않으며 Cloud 모델이나 embedding-only 모델을 오프라인 생성에 사용하지 않습니다.

### 3.3 Citation 검토

1. Citation의 origin이 `Daon 지식`인지 `Raw Source`인지 확인합니다.
2. producer/version, Source 또는 지식 Version, authority·quality, locator를 확인합니다.
3. 질문에 사용한 Context 밖의 Citation은 거부됩니다.
4. Citation이 없거나 `unverified`이면 결과를 최종 승인하지 않습니다.

![혼합 Knowledge Context와 Citation](../../03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/foundation-b3-knowledge-context-browser.png)

### 3.4 품질·비용·지연 조정

- 품질: 권위가 높은 최신 Daon 지식과 업무에 직접 관련된 Raw Source를 우선 선택하고 Citation을 검토합니다.
- 비용: 불필요한 Source와 긴 출력 분량을 줄이고, 같은 기능을 여러 Provider에 반복 실행하지 않습니다.
- 지연: 처리 중인 Source가 `사용 가능`이 될 때까지 기다리고, 대규모 Source는 질문 범위를 좁힙니다.
- 안정성: 실행 중 Model을 자동 교체하지 않습니다. 선택이 stale하면 새 설정 Snapshot으로 다시 확인합니다.
- 재현성: Context, Model, 생성 설정, Run, Output Version의 digest 계보를 유지합니다.

### 3.5 운영 기준

1. 일반 사용자는 조직 정책과 License 상태를 읽고 허용된 기능만 사용합니다.
2. 조직 관리자는 Credential 설정과 License 적용에서 필요한 Step-up을 수행합니다.
3. 외부 전송은 조직 Egress 정책과 승인 Snapshot을 통과한 항목만 허용합니다.
4. 승인 전 자동 전송, 자동 Provider fallback, 자동 충돌 덮어쓰기는 사용하지 않습니다.
5. 오류 조사 시 원문 Source·응답·Token을 Evidence나 Log에 남기지 않습니다.

## 4. 예상 결과

- 동일 질문에서 Daon 지식과 Raw Source가 하나의 Knowledge Context로 결속되고 origin·권위·Version은 Citation에 남습니다.
- 선택 Provider·Model은 Generation Settings와 RunSnapshot, Output Version에 동일하게 기록됩니다.
- 지원하지 않는 입력 형식은 Source 전체를 삭제하거나 숨기지 않고 해당 Run만 안전하게 중단합니다.
- 비용·지연을 위해 대표 Provider 하나로 기능을 시험해도 나머지 Provider의 연결 상태와 역할 설정은 별도로 확인할 수 있습니다.
- 불확실한 답변은 `unverified` 상태와 검토 조건을 유지합니다.

## 5. 제한·오류 대응

- `DAON_KNOWLEDGE_UNAVAILABLE`: 승인·등록·유효기간이 맞는 Daon 지식을 선택합니다. 자동 Raw fallback은 없습니다.
- `KNOWLEDGE_CONTEXT_STALE`: Source·지식 Version을 다시 불러와 새 Context를 만듭니다.
- `MODEL_SELECTION_STALE`: 현재 Deployment·Model을 다시 선택하고 설정을 확인합니다.
- `LOCAL_MODEL_UNAVAILABLE`: Ollama 서비스와 설치된 completion 모델 상태를 운영상태에서 확인합니다. Daon 화면에서 설치를 시도하지 않습니다.
- `MODEL_INPUT_CAPABILITY_UNAVAILABLE`: Source는 유지하고 지원 Model 또는 형식별 Representation 준비를 기다립니다.
- `CITATION_RESPONSE_INVALID`: 결과를 승인하지 말고 질문 Context와 Citation 계보를 다시 확인합니다.
- Provider 오류가 발생해도 다른 Provider로 자동 전환하지 않습니다. 필요한 경우 사용자가 설정에서 명시적으로 선택합니다.

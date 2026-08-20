# Daon Getting Started

- Release: 1.0.0
- 언어: 한국어(ko-KR)
- 대상: 처음 Daon Workspace를 사용하는 사용자
- 범위: 공개 안내와 로그인 후 조직 전용 절차를 구분합니다.

> 현재 Release의 첫 성공 경로 중 `Notebook 생성`과 `Notebook 홈`은 Phase D 준비 중입니다. 로그인 연결은 Phase E에서 마지막에 결합합니다. 지금 검증된 범위는 선택된 Test Notebook의 3열 화면에서 Source를 추가하고, 질문과 Citation을 확인하고, 지원되는 Studio 산출물을 생성·저장하는 흐름입니다.

## 1. 목적

Daon의 기본 작업 흐름인 `Notebook 생성 → Source 추가 → 질문 → Citation 확인 → Studio 산출물 생성`을 이해하고, 현재 사용할 수 있는 단계와 준비 중인 단계를 구분합니다. Daon은 Source의 형식을 임의로 제한하지 않으며, Daon 생성 지식과 사용자가 추가한 Raw Source를 함께 사용할 수 있습니다.

## 2. 접근 경로

### 공개 범위

설정 메뉴의 `사용자 설명서`는 문서 제목 검색, Web 읽기, Release 확인, DOCX·PDF 다운로드를 제공합니다. 공개 범위에는 제품 개요, 화면 구조, 안전한 오류 대응이 포함됩니다.

### 로그인 후 조직 전용

Workspace의 Source, 대화, Studio 산출물, Provider 상태와 조직 정책은 로그인 후 현재 조직·Workspace 권한으로만 접근합니다. 개발 검증 Harness는 로그인 우회 기능이 아니며 운영 Route에 포함되지 않습니다.

## 3. 조작

### 3.1 Notebook 생성

1. 현재 Release에서는 Notebook 홈과 `새 Notebook 만들기`가 **준비 중**입니다.
2. 제품에서 임의 Notebook이 자동 생성되거나 선택되지 않습니다.
3. 개발·검증에서는 명시된 Test Notebook Context의 3열 화면만 사용합니다.

예상 클릭 위치: 향후 Notebook 홈 오른쪽 위 `새 Notebook 만들기`. 현재 화면에는 이 Action이 없어야 정상입니다.

### 3.2 Source 추가

1. 선택된 Notebook의 왼쪽 `Source` 패널에서 `Source 추가`를 선택합니다.
2. 현재 실제 Web 경로는 PDF 등록과 처리 상태 확인을 지원합니다. Local Offline 경로는 PDF·plain text·Markdown의 명시적 파일 Import를 검증했습니다.
3. 처리 상태가 `사용 가능`이 된 Source만 질문 Context에 선택합니다.
4. Daon 지식을 사용할 경우 승인·등록·Version 상태를 함께 확인합니다.

입력 조건: 사용자가 접근 권한을 가진 Source여야 하며, 처리·색인 또는 LLM 표현 준비가 끝나야 합니다. 형식별 Adapter가 아직 없거나 선택 모델이 입력을 이해하지 못하면 Source는 보존되지만 해당 Run만 안전하게 중단됩니다.

![현재 Source·대화·Studio 3열 화면](../../03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/web-final-ui/01-workspace-default-1920x1080.png)

### 3.3 질문

1. 가운데 `대화·실행` 패널의 질문 입력란을 선택합니다.
2. 선택한 Source 또는 Daon 지식에 근거한 구체적인 질문을 입력합니다.
3. 추가 인증이 요구될 때만 현재 비밀번호를 입력하고 승인 절차를 완료합니다.
4. `질문 실행`을 선택합니다.

입력 조건: 선택 Context와 질문이 모두 필요합니다. LLM은 연결된 Provider의 사용 가능한 Model 중 사용자가 선택한 Model을 사용하며 자동으로 다른 Provider로 바꾸지 않습니다.

### 3.4 Citation 확인

1. 답변 아래 Citation의 Source 이름, Version, 근거 위치와 origin을 확인합니다.
2. PDF Citation은 페이지를, Daon 지식은 지식 구간을 표시합니다.
3. Citation을 열 수 없는 형식이면 답변 근거를 다른 Source로 가장하지 않고 Renderer 준비 필요 상태를 표시합니다.
4. 상충 근거가 있으면 한쪽을 숨기지 말고 답변과 Citation을 함께 검토합니다.

### 3.5 Studio 산출물 생성

1. 오른쪽 `업무 Studio`에서 지원되는 유형을 선택합니다.
2. 현재 구현된 유형은 `근거 기반 보고서`, `제약·준수 점검표`, `비교·데이터 표`, `지식 구조도`, `업무 문서 초안`입니다.
3. 목적·독자·분량·구성·출력 형식·검토 조건을 입력하고 설정을 확인합니다.
4. 생성 후 Citation, Version, 검토 상태를 확인합니다.
5. 승인된 산출물만 현재 정책이 허용하는 형식으로 Export합니다.

![보고서 생성 설정 화면](../../03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/web-final-ui/02-report-settings-1920x1080.png)

`슬라이드`, `인포그래픽`, `플래시카드`, `퀴즈`, `AI 오디오`, `동영상`은 준비 중 Tile이며 성공을 가장하지 않습니다.

## 4. 예상 결과

- Source는 종류·처리 상태·권위·Version과 함께 목록에 표시됩니다.
- 답변에는 선택 Context 밖의 근거가 섞이지 않고, Citation에 Source/지식 origin과 근거 위치가 표시됩니다.
- Studio 산출물은 저장된 산출물 Library에 유형, 제목, Source 수, Version, 생성 시각과 상태로 표시됩니다.
- 생성 설정과 선택 Model은 Run·Output Version 계보에 고정됩니다.
- Notebook 홈이 완성되기 전에는 새 Notebook 생성 성공 화면이 나타나지 않습니다.

## 5. 제한·오류 대응

- `WORKSPACE_ADAPTER_UNAVAILABLE`: Workspace 연결을 확인하고 다시 시도합니다.
- `SOURCE_LIST_FAILED`: Source 패널의 `다시 시도`를 사용합니다. 기존 산출물은 삭제하지 않습니다.
- `MODEL_INPUT_CAPABILITY_UNAVAILABLE`: Source는 유지하고 해당 형식을 지원하는 Model 또는 준비된 Representation을 선택합니다.
- `TEXT_MODEL_NOT_SELECTED`: 설정의 `LLM 설정`에서 사용 가능한 Model을 선택합니다.
- `CITATION_RENDERER_UNAVAILABLE`: 답변을 확정하지 말고 원문을 지원하는 Renderer가 준비될 때까지 검토 상태를 유지합니다.
- License 만료·한도 도달: 신규 질문·생성은 중단될 수 있으나 계약이 허용한 기존 조회·Export는 계속 사용할 수 있습니다.
- 내부 주소, Token, 비밀번호, License 원문을 오류 보고에 붙이지 않습니다. 화면에 표시된 Safe code와 조치만 관리자에게 전달합니다.

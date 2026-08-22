# Daon NotebookLM 동등 기능 상세 설계서

## 문서 상태

- 문서 구분: 요구사항 확정 후 신규 상세 설계안
- 버전: 1.0-draft
- 상태: 신산님 승인됨 (API·Worker·Export 계약 확장 포함)
- 대상: Daon 사용자형 Notebook
- 기준: Google NotebookLM의 현재 사용자 흐름과 기능
- 화면 기준: 현재 3면 Workspace와 Studio 카드 배치 유지

## 1. 제품 목표

Daon은 장난감이나 시각 Prototype이 아니라, Google NotebookLM과 최대한 동일한 운영형 Notebook 제품을 제공한다. 사용자는 Notebook 안에서 Source를 등록하고, 대화하며, Studio 산출물을 만들고, 결과를 Library에서 관리한다.

NotebookLM과의 차이는 Source 선택지에 다음 두 연결형 Source를 추가하는 것이다.

1. MCP 서버
2. Daon 승인 지식

## 2. 불변 사용자 흐름

`Notebook 생성 → Source 추가/선택 → 대화 또는 Studio 작업 → 결과 확인·저장·삭제`

현재 화면의 세 영역은 유지한다.

- 왼쪽: Source·지식·권위
- 가운데: 대화·실행
- 오른쪽: 업무 Studio·Library

화면을 다시 배치하거나 카드 위치를 변경하지 않는다. 기능 연결과 상태·문구·오류 표시만 실제 동작에 맞게 보완한다.

## 3. Source 설계

### 3.1 Source 유형

일반 Source:

- 파일 업로드: PDF, 이미지, 문서, 텍스트, Markdown, CSV, 프레젠테이션, 오디오 등 지원 가능한 형식
- 웹사이트 URL
- Google Drive 파일
- 복사한 텍스트
- 웹 검색/Fast Research 결과
- YouTube 등 지원되는 URL 기반 자료

연결형 Source:

- MCP 서버: 사용자가 서버를 등록하고 연결을 유지하며 Notebook에서 선택해 사용하고 즉시 해제·삭제
- Daon 승인 지식: 시스템이 관리하는 고정 지식. MCP와 같은 상태·선택 모양을 사용하지만 사용자 등록·삭제는 제공하지 않음

지원 형식은 PDF로 제한하지 않는다. 실제 지원 여부는 Provider·Parser가 표시하는 지원 Matrix에 따라 결정하며, 지원하지 않는 형식은 등록 전에 명확한 오류로 안내한다.

### 3.2 등록·삭제 수명

- Source 등록은 사용자가 확정하면 즉시 현재 Notebook에 등록한다.
- 등록 시 Notebook 삭제와 원본 데이터의 삭제 관계를 선택한다.
  - `Notebook과 함께 삭제`: Notebook 삭제 시 Source 원본·인덱스도 함께 삭제
  - `Notebook 삭제와 무관하게 보관`: Notebook 삭제 후에도 원본을 보관해 다시 등록할 수 있음
- Notebook이 존재하는 동안에는 두 유형 모두 자동 삭제하지 않는다.
- Source 삭제는 사용자가 실행하면 즉시 삭제한다.
- 삭제 요청, 승인, 30일 유예, Legal Hold, 자동 정리는 사용하지 않는다.
- 외부 원본이 없어졌거나 접근 권한이 사라진 Source는 자동 삭제하지 않고 `사용 불가`로 표시한다.
- `사용 불가` Source는 대화·Studio 검색 대상에서 제외한다.
- 사용자는 `사용 불가` Source를 남겨두거나 직접 삭제할 수 있다.
- Notebook 삭제 시 그 Notebook에 귀속된 일반 Source와 산출물을 함께 삭제한다.
- MCP 연결을 삭제하면 로컬 Connector 등록과 Notebook 바인딩만 즉시 제거하며 외부 서버 원본 데이터는 삭제하지 않는다.
- Daon 승인 지식은 고정 시스템 자산이므로 사용자 삭제·등록 API와 삭제 버튼을 제공하지 않는다.

### 3.3 동일 데이터 중복 방지

- 파일 내용의 SHA-256 `content_digest`를 등록 시 계산한다.
- 동일 Digest의 원본 데이터는 Object Storage에 두 번 저장하지 않는다.
- Notebook별 Source 연결(`notebook_source_binding`)은 독립적으로 만든다. 한 Notebook의 목록·삭제·권한은 다른 Notebook에 자동으로 노출되지 않는다.
- 동일 데이터의 보관 유형이 다르면 원본은 공유하되, Notebook별 삭제 정책은 각각 적용한다.
- Notebook 삭제 시 `Notebook과 함께 삭제` 연결만 제거·정리한다. 다른 Notebook 연결 또는 `Notebook 삭제와 무관하게 보관` 정책이 남아 있으면 원본은 유지한다.

### 3.4 Source 선택과 근거

- Source 패널에서 개별 Source를 선택·해제한다.
- 대화와 Studio 작업은 선택된 사용 가능한 Source만 사용한다.
- 답변과 산출물에는 Source·페이지·문단·시간 등 가능한 인라인 근거를 표시한다.
- Source가 선택되지 않은 일반 작업 질문은 일반 LLM 대화로 처리한다. `근거가 부족하여 답변할 수 없습니다`를 기본 응답으로 사용하지 않는다.

## 4. MCP·Daon 승인 지식 Connector

두 연결형 Source는 동일한 상태·선택 인터페이스를 사용하되 소유권과 수명주기는 분리한다.

- 서버 이름, 제공자, 연결 상태, 마지막 확인 시각 표시
- MCP는 등록·재연결·연결 해제·즉시 삭제를 제공한다. Daon 승인 지식은 시스템이 관리하며 재연결·상태만 제공한다.
- MCP 등록 정보와 Notebook별 연결 바인딩은 Postgres에 영속화한다. API 재시작·컨테이너 재생성 후에도 동일 Workspace에서 등록 상태와 `사용 불가` 상태를 복구한다.
- Connector 영속화 레코드는 Workspace 소유권으로 격리하고, MCP 삭제는 해당 Workspace의 로컬 바인딩과 등록 정보만 즉시 제거한다.
- Notebook에서 사용할 Resource/도구 선택
- 장애·권한 만료 시 `사용 불가` 표시
- 연결 해제는 현재 Notebook의 사용 연결만 제거

첫 MCP 샘플 서버는 국가법령정보센터 `https://open.law.go.kr/`로 한다. 인증정보와 내부 서버 주소는 브라우저에 노출하지 않고 BFF/Connector 계층에서 처리한다.

## 5. 대화 기능

- NotebookLM처럼 Source 근거 기반 질문, 요약, 비교, 설명을 지원한다.
- 질문 컨텍스트에는 현재 Notebook과 선택 Source가 표시된다.
- 일반 업무 진행 상담도 허용한다.
- Source에 없는 내용을 근거가 있는 것처럼 생성하지 않는다. 필요한 경우 “현재 Source에는 해당 내용이 없으며, 일반 지식 답변으로 안내한다”고 구분한다.
- 답변은 한국어를 포함한 선택 출력 언어를 지원한다.

## 6. 업무 Studio 기능

현재 Studio 카드 배치는 유지하되, 각 카드는 실제 NotebookLM Studio 동작을 수행한다.

- 근거 기반 보고서: Source 요약·분석·인용·경고가 포함된 보고서
- 제약·준수 점검표: 기준·항목·판정·근거·조치가 포함된 점검표
- 비교·데이터 표: Source 간 기준·값·차이·누락을 구조화한 표
- 지식 구조도: 개념·관계·근거를 시각화한 구조도/마인드맵
- 업무 문서 초안: Source 기반 문서 초안
- 슬라이드: 발표용 Slide Deck
- 인포그래픽: Source 내용을 시각 요약한 Infographic
- 플래시카드: 난이도·주제·정답 설명이 포함된 학습 카드
- 퀴즈: 문제·선택지·정답·해설이 포함된 퀴즈
- AI 오디오: Source 기반 Audio Overview
- 동영상: Source 기반 Video Overview

각 기능은 생성 전 선택 Source·출력 언어·형식·길이·사용자 지시를 설정할 수 있고, 생성은 백그라운드 작업으로 수행한다. 결과는 Library에 저장하며 열기·다운로드·삭제가 가능하다. 생성 결과에는 사용한 Source와 생성 시점의 근거 계보를 표시한다.

### 6.1 API·Worker·Export 계약 별도 개발 범위

Studio 카드를 화면에 표시하는 것과 실제 산출물을 생성·저장·다운로드하는 것은 별개의 기능이다. 현재 연결된 Studio 유형 외에 슬라이드·인포그래픽·플래시카드·퀴즈·AI 오디오·동영상 등을 추가하려면 API, Worker, Export 계층의 계약을 별도 개발 범위로 관리한다. 화면 카드만 허용 목록에 추가하고 하위 계약을 구현하지 않는 방식은 완료로 인정하지 않는다.

#### API 계약

- `POST /studio-generation-requests`는 `notebook_id`, `output_type`, 선택된 `source_version_ids`, 필요한 `grounded_run_id`, 출력 언어·형식·길이·사용자 지시, `idempotency_key`를 받는다.
- 요청은 즉시 파일을 반환하지 않고 `job_id`, `status=queued`, `output_type`을 반환한다.
- `GET /studio-generation-requests/{job_id}`는 `queued`, `leased`, `generating`, `completed`, `failed`, `unavailable` 상태와 안전한 오류 코드·재시도 가능 여부를 반환한다.
- 완료된 결과는 산출물·버전·Citation·Source 계보를 조회할 수 있어야 하며, Notebook·Workspace·Tenant 범위를 넘겨 조회할 수 없다.
- 동일 `idempotency_key`의 중복 요청은 새 작업을 만들지 않는다.
- 지원하지 않는 유형·형식은 `OUTPUT_TYPE_NOT_SUPPORTED`, `EXPORT_FORMAT_UNSUPPORTED` 등 명시적인 계약 오류로 반환한다.

#### Worker 계약

- Worker는 `queued` Job을 lease한 뒤 선택된 Source와 Citation만 읽고 유형별 프롬프트·출력 Schema로 Provider를 호출한다.
- Provider 응답은 유형별 구조화 결과로 검증한 뒤 표준 `content`, `citations`, `source_lineage`, `metadata` 구조로 저장한다.
- Timeout, 재시도·Backoff, 중복 실행 방지, Schema 오류, Provider 미선택, Dead-letter 처리를 계약에 포함한다.
- 유형별 최소 결과 계약을 둔다. 예를 들어 퀴즈는 문제·선택지·정답·해설·Citation, 슬라이드는 슬라이드 순서·제목·본문·Citation을 포함해야 한다.
- Audio/Video는 실제 Media Provider가 연결되지 않은 경우 결과를 가장하지 않고 `unavailable`로 종료한다.

#### Export 계약

- 산출물 유형별 허용 형식과 MIME을 명시한다. 보고서·초안은 PDF/DOCX, 표·점검표는 XLSX/CSV/PDF, 구조도·인포그래픽은 SVG/PNG/PDF/JSON, 퀴즈·플래시카드는 JSON/CSV/PDF를 기본으로 한다.
- Export API는 유형·형식 조합을 검증하고 파일명·Content-Type·크기·Hash·Object Storage 위치·다운로드 만료 정보를 반환한다.
- PDF·문서·표·JSON 등 모든 형식에서 가능한 범위의 Citation과 생성 시점·Source 계보를 보존한다.
- 실제 MP3/MP4 Provider와 미디어 저장소가 없는 Audio/Video는 다운로드 가능한 가짜 파일을 만들지 않고 `unavailable`로 표시한다.

#### 공통 완료 조건

- 하나의 Studio 유형이 API 요청 → Worker 생성 → DB/Library 저장 → Export/다운로드까지 실제로 연결되어야 한다.
- API 계약 테스트, Worker 구조화 출력 테스트, Provider 모킹 테스트, DB 계보·Notebook 격리 테스트, Export 파일·MIME 검증, 브라우저 클릭 검증을 유형별로 기록한다.
- UI에 카드가 보인다는 사실만으로는 해당 유형을 완료 처리하지 않는다.

## 7. 저장·삭제·오류 표시

- 일반 Source와 Studio 결과물은 Notebook 소유 자료로 저장한다.
- 사용자가 삭제한 Source·결과물은 즉시 삭제한다.
- 데이터가 외부에서 사라진 경우에만 `사용 불가`를 표시한다.
- 오류 배너에는 사용자 조치(재시도, 연결 확인, 삭제)를 포함한다.
- 성공 여부를 확인하지 못한 상태에서 목록을 빈 상태로 바꾸지 않는다.

## 8. 운영·기술 경계

- 브라우저는 same-origin BFF 상대 경로만 호출한다.
- MCP·웹·Drive Connector의 비밀값과 외부 주소는 서버/BFF에만 둔다.
- 실제 구현은 로컬 검증 → Git Push → ysna-server 격리 배포 → 통합 테스트 순서로 진행한다.
- 운영 배포는 전체 테스트와 신산님 최종 승인 후에만 수행한다.

## 9. 승인 전 미결정 사항

- NotebookLM 기능별 지원 형식과 Daon Provider별 처리 가능 범위 Matrix
- 국가법령정보센터 MCP의 실제 API/인증/쿼터 계약
- Studio 각 산출물의 파일 형식과 다운로드 형식
- MCP Resource/Tool 선택을 현재 화면 카드와 Source 패널에 표시하는 세부 문구

위 항목은 구현자가 임의로 결정하지 않고 작업계획서 작성 전에 확정한다.

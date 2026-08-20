# Daon 사용자 설명서

- Release: 1.0.0
- 업데이트: 2026-08-20
- 언어: 한국어(ko-KR)
- 대상: Daon 일반 사용자, 검토자, 승인자, 조직 관리자
- 범위: 공개 안내와 로그인 후 조직 전용 절차를 분리합니다.

## 1. 목적

현재 Release에서 실제 구현된 로그인, Notebook 홈, 선택한 Notebook의 3열 Workspace, 설정, 권한, 상태, Version, 검토·승인·Export 절차를 한 곳에서 찾도록 합니다. 아직 실제 제품 E2E로 검증하지 않은 항목은 완료 기능으로 설명하지 않습니다.

## 2. 접근 경로

### 공개 범위

`설정 → 사용자 설명서`에서 문서 검색, Release 확인, Web 읽기, DOCX·PDF 다운로드를 사용할 수 있습니다. 공개 문서에는 제품 구조와 안전한 사용 원칙만 포함합니다.

### 로그인 후 조직 전용

Source 원문, 질문·답변, Citation 상세, Studio 산출물, Provider 설정, 운영상태, 조직 정책, License 적용은 현재 Session과 Workspace 권한을 검사합니다. 일반 사용자는 License 상태를 읽을 수 있지만 적용 Action은 볼 수 없습니다.

## 3. 조작

### 3.1 Notebook 홈

1. 로그인 성공 후 Notebook 홈으로 이동합니다.
2. `새 Notebook`에서 제목과 선택 설명을 입력하거나 기존 Notebook 카드를 선택합니다.
3. 검색·최근 수정/제목 정렬·Grid/List 전환으로 Notebook을 찾습니다.
4. 새 Notebook은 빈 3열 화면으로, 기존 Notebook은 저장된 Source·대화·산출물 Context를 서버에서 재검증한 뒤 3열 화면으로 엽니다.
5. 로그아웃·Session 만료·Workspace 변경 시 이전 Notebook 화면은 즉시 숨기고 재검증합니다.

### 3.2 선택한 Notebook의 3열 화면

1. 왼쪽 `Source`에서 Raw Source 또는 승인된 Daon 지식을 선택합니다.
2. 가운데 `대화·실행`에서 질문하고 답변의 Citation을 검토합니다.
3. 오른쪽 `업무 Studio`에서 산출물 유형과 저장된 Library를 관리합니다.
4. 초안을 편집할 때 가운데 영역이 Editor로 전환되지만 왼쪽 Source와 오른쪽 Studio 위치는 유지됩니다.

### 3.3 설정

상단 `설정`을 선택하면 화면을 밀어내지 않는 메뉴가 열립니다.

- `LLM 설정`: 9 Provider의 설정 여부, Endpoint 안전 상태, Deployment·Model, 연결 시험과 기본 역할을 확인합니다. Credential 원문은 다시 표시하지 않습니다.
- `출력·버전`: 구현된 Studio 유형별 기본 Export 형식과 append-only Version 저장 원칙을 확인합니다.
- `동기화·승인`: Preview 항목을 명시적으로 선택하고 필요한 Step-up을 거쳐 승인합니다. 자동 전체 승인이나 자동 전송은 없습니다.
- `조직 정책`: 조직이 강제한 전송·검토 정책과 Workspace 적용 결과를 읽기 전용으로 확인합니다.
- `라이선스`: Edition, 안전한 License ID 일부, 기간, 기능, 한도·사용량·잔여와 경고를 확인합니다.
- `사용자 설명서`: 문서 Hub를 엽니다.

Notebook 홈의 `설정` 메뉴에서도 `화면 설정`, `라이선스`, `사용자 설명서`로 이동할 수 있습니다.

### 3.4 화면 설정

1. Theme에서 `시스템 설정`, `밝게`, `어둡게` 중 하나를 선택합니다.
2. `시스템 설정`은 운영체제의 Light/Dark 변경을 반영합니다.
3. `화면 설정 초기화`는 화면 Preference만 초기화합니다.
4. Notebook, Source, 대화, 산출물 데이터는 변경되지 않습니다.

### 3.5 운영상태

1. App Bar의 `운영상태`를 선택합니다.
2. Provider, API, Storage, Sync, Queue 상태와 마지막 확인 시각을 봅니다.
3. `주의`나 `오류` 항목의 안전한 복구 Action만 실행합니다.
4. 상세 화면에 내부 주소·Port·Stack이 보이면 캡처·전달하지 말고 보안 담당자에게 Safe code만 알립니다.

### 3.6 Version·검토·승인

1. 산출물을 생성하면 첫 Output Version이 저장됩니다.
2. 편집은 기존 Version을 덮어쓰지 않고 새 Version을 추가합니다.
3. 검토자는 Citation과 `unverified` 경고, 조직 검토 조건을 확인합니다.
4. 승인자는 현재 권한과 Step-up이 요구될 때만 승인합니다.
5. 승인 만료, 권한 축소, Version 충돌에서는 전송과 쓰기가 중단됩니다.

### 3.7 Export

1. Library에서 승인된 산출물을 선택합니다.
2. `내보내기`에서 현재 유형과 조직 정책이 허용하는 형식을 선택합니다.
3. 브라우저는 same-origin 다운로드만 사용합니다.
4. License 만료나 신규 생성 한도 도달 상태에서도 License 계약이 허용하면 기존 산출물 조회와 Export는 유지됩니다.

## 4. 예상 결과

- 화면은 1920×1080 기준, 기본 12px Typography와 동일한 Violet 시각 체계를 유지합니다.
- Modal은 최초 Focus, Tab 순환, Escape 닫기, 닫은 뒤 호출 Button Focus 복귀를 지원합니다.
- 설정·운영상태의 오류는 관련 영역에 Safe message로 표시되며 원문 Stack이나 내부 주소는 표시되지 않습니다.
- Version은 append-only 계보를 유지하고 승인·동기화는 명시적 사용자 동작 후에만 진행됩니다.
- 권한이 없는 일반 사용자의 License Upload/Apply control은 0개입니다.

## 5. 제한·오류 대응

- 추가 Studio 6종은 disabled `준비 중` 상태입니다.
- Source가 없을 때는 좁은 일반 대화만 허용합니다. 사실 질문은 Source 또는 승인 지식 Context를 먼저 선택합니다.
- Provider 연결 시험 성공은 외부전송 정책 승인이나 Source 기반 생성 성공을 의미하지 않습니다.
- `CURRENT_ACCESS_DENIED`: 현재 역할 또는 Workspace 범위를 확인합니다. 우회하지 않습니다.
- `SYNC_APPROVAL_REQUIRED`: Preview와 선택 항목을 다시 확인하고 필요한 Step-up을 수행합니다.
- `SYNC_VERSION_CONFLICT`: 자동 덮어쓰지 말고 충돌 화면에서 명시적으로 선택합니다.
- `LICENSE_UNAVAILABLE`: 다시 불러오고 계속 실패하면 조직 관리자에게 Safe code만 전달합니다.
- `LICENSE_DOCUMENT_INVALID`: 조직 관리자는 서명된 문서 형식과 크기를 확인합니다. 일반 사용자는 적용을 시도하지 않습니다.
- `EGRESS_POLICY_DENIED`: 조직의 허용 Provider·목적지·데이터 분류·마스킹 조건을 확인합니다. 내부 주소를 직접 입력해 우회하지 않습니다.
- 다운로드 실패 시 다른 내부 경로를 직접 조합하지 말고 Hub의 allowlisted DOCX/PDF Action으로 다시 시도합니다.

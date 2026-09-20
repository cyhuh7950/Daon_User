# 사용자 관리·설정 화면 개선 설계

## 상태

- 상태: 승인된 설계 초안
- 기준 branch: `codex/next-user-development`
- 기준 commit: `b23e83ec15718ff8858140ab90e393066e32ec56`
- 요구사항: 설정 화면 복귀 동선, LLM Provider 대비, 사용자 관리 테이블·모달, 사용자 비밀번호 초기화, 제품 디자인 표준 준수

## 1. 목표와 범위

관리자와 일반 사용자가 설정 화면에서 Notebook으로 쉽게 돌아오고, LLM Provider 목록과 사용자 관리 정보를 읽기 쉽게 확인하도록 Web 화면을 개선한다. 사용자 관리에는 기존 계정 상태·역할·삭제 동작을 유지하면서 테이블형 관리와 안전한 비밀번호 초기화 흐름을 추가한다.

범위 밖:

- 사용자 ID·이메일·역할 데이터 모델의 임의 변경
- 보호된 `admin` 계정의 일반 사용자 비밀번호 초기화 경로 편입
- 비밀번호·hash·token의 화면·로그·응답 노출
- 기존 Provider 연결·라이선스·조직 정책의 API 의미 변경

## 2. 화면 설계

### 2.1 공통 복귀 동선

- 대상: LLM 설정, 라이선스, 사용자 설명서
- 현재 라이선스·사용자 설명서에는 이미 `/notebooks` 링크가 있으므로 중복 렌더링하지 않는다.
- LLM 설정의 페이지 헤더에 기존 설정 화면과 동일한 `Notebook으로` 링크를 추가한다.

### 2.2 LLM Provider 카드

- 선택되지 않은 카드의 Provider 이름·설명·상태 텍스트는 어두운 표면의 선택 카드와 구분되면서 밝은 배경에서 WCAG AA 본문 대비를 만족하도록 조정한다.
- 선택된 카드의 배경·focus·선택 상태 표현은 유지한다.
- 상태는 색상만으로 구분하지 않고 기존 텍스트를 유지한다.

### 2.3 사용자 관리 목록

- 기존 카드 목록을 테이블형 목록으로 변경한다.
- 상단 action: 선택 건수, 선택 삭제, 새로고침, 사용자 등록.
- 검색·상태 필터·조회 결과 수를 제공한다.
- 컬럼: 선택, 사용자, 이메일, 상태, 역할, 관리.
- 사용자·이메일·상태·역할은 기존 API 값을 그대로 사용한다.
- 위험 action인 삭제는 기존 확인 절차와 보호 계정 제한을 유지한다.

### 2.4 사용자 수정 모달

- 대상 사용자 ID와 이메일을 읽기 전용 정보로 표시한다.
- 상태 select와 관리자·전문가 역할 checkbox를 제공한다.
- `취소`, `수정` action을 제공한다.
- 모달 focus, label, 오류 status와 keyboard escape/submit 동작을 기존 패턴에 맞춘다.

## 3. 비밀번호 초기화 계약

- 일반 사용자 행의 관리 action에서 `비밀번호 초기화`를 실행한다.
- BFF는 same-origin 보호 route로 API의 관리자 전용 reset route를 전달한다.
- API는 현재 로그인 principal이 system admin인지 확인하고, 보호된 `admin` 계정과 자기 자신을 일반 reset 대상으로 허용하지 않는다.
- 대상 계정은 local active 계정이어야 하며, 이메일이 없는 계정은 안전한 사용자 오류로 거부한다.
- reset은 기존 password reset token·메일 전달 계약을 재사용한다. 화면과 API 응답에는 임시 비밀번호나 token을 반환하지 않는다.
- 성공 시 대상 사용자의 기존 session과 refresh family를 폐기하고 reset 메일 발송 상태만 반환한다.
- 사용자는 메일의 reset token으로 새 비밀번호를 설정한다.
- reset 요청·성공·거부는 기존 audit 계약과 동일한 trace/policy 경계를 사용한다.

## 4. 디자인 표준

- `D:\Project\PMO\docs\PRODUCT_DESIGN_STANDARD.md`의 Pretendard fallback과 의미 토큰을 유지한다.
- 기준 본문·폼은 최소 12px, 보조 설명은 10px 이상, 제목은 16px 이상으로 조정한다.
- 본문 대비 4.5:1 이상, focus-visible, label, status/alert를 확인한다.
- 기준 viewport: 1920×1080, 1440×900, 430×844.
- 모바일에서는 테이블을 필요한 열만 표시하거나 허용된 가로 스크롤 영역으로 제한한다.

## 5. 검증 기준

- UI: Notebook link, Provider unselected contrast, table columns/filter/actions, edit modal, reset success/error states.
- API: non-admin 403, protected admin rejection, missing email rejection, reset token delivery contract, session/refresh revocation, audit event.
- 정적·단위·계약 테스트를 먼저 RED/GREEN으로 수행한다.
- Web build, Product UI boundary, keyboard/focus, 실제 3 viewport browser interaction을 수행한다.
- 실제 Provider 호출·실제 사용자 비밀번호·운영 데이터 변경은 별도 인수 범위로 기록한다.

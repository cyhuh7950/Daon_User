# 라우터 Auto 모델 설계

상태: 신산님이 `auto`만 포함하고 `combo`는 제외하는 방향과 변경안을 2026-10-04 대화에서 승인. 이 문서는 구현 전 서면 검토 대상이다.

## 목적과 범위

관리자는 라우터 연결에서 `Auto`를 다른 모델처럼 명시적으로 선택할 수 있다. 업스트림 `/models`에 Auto가 없어도 라우터가 해당 모델 ID를 지원하면 선택 가능하다. 적용 대상은 OmniRoute, OpenRouter, 관리자가 라우터로 지정한 OpenAI 호환 연결(Media Bridge Server 포함)이다. 일반 Provider에는 Auto를 임의 추가하지 않는다. `combo` 자동 선택지, 새로운 라우팅 알고리즘, 기존 Key·Endpoint 변경, Oracle 배포는 제외한다.

## 연결 구분과 실제 모델 ID

`system_provider_connections`에 nullable `auto_model_id`를 추가한다. 값이 있으면 그 연결은 Auto 모델을 제공하는 라우터이고, 없으면 일반 Provider다. 이 한 값이 라우터 구분과 호출할 실제 ID를 함께 표현한다. 기존 OmniRoute에는 `auto`, 기존 OpenRouter에는 `openrouter/auto`를 backfill한다. OpenRouter의 ID는 `/models`에도 나타나고, OmniRoute의 `auto`는 실제 `/models` 목록에는 없다. 다른 기존 연결은 이름·URL만으로 라우터라고 추정해 자동 변경하지 않는다. Media Bridge Server 같은 기존 `CUSTOM` 연결은 관리자가 연결 설정에서 라우터를 명시적으로 지정하고 `auto_model_id=auto`로 저장할 수 있다. 새 `CUSTOM` 연결도 같은 방식이다.

`auto_model_id`는 기존 모델 ID 길이·문자 검증을 통과해야 하며, 관리자만 바꿀 수 있다. 고정 OmniRoute/OpenRouter의 값은 서버에서 각각 정해 UI에서 임의 수정하지 않는다. 기존 연결의 `provider_code`, `adapter_type`, Credential, 허용 모델, Workspace 기본값은 migration으로 재작성하지 않는다. Media Bridge Server를 설정할 때도 기존 Key를 재입력하거나 교체하지 않는다.

## 카탈로그·허용 모델·화면

Auto는 화면에서 `Auto`라고 표시하되 저장·선택·실행에는 연결의 실제 `auto_model_id`를 사용한다. `/models`에 같은 ID가 있으면 중복 생성하지 않는다. 없으면 `logical` Auto 모델로 카탈로그에 한 행을 유지해 기존 허용 목록과 Workspace 기본 모델의 FK 계약을 충족한다. 조회 결과와 별도로 만든 항목임을 UI에 표시한다. 카탈로그 새로고침은 Auto 행과 기존 허용 목록을 임의 삭제하거나 새 모델을 자동 허용하지 않는다.

Auto는 허용 목록에서 한 개 모델로 계산하며 다른 허용 모델과 함께 선택할 수 있다. 선택하지 않은 Auto는 호출하지 않는다. 모델을 지정하지 않은 요청의 기준 모델 동작은 Auto를 명시적으로 선택한 요청과 별개다. 이번 변경에서 모델 미지정 경로의 동작이나 기존 기본 모델 설정은 바꾸지 않는다. `combo`를 합성하거나 특별 취급하지 않는다. 업스트림이 일반 모델 목록에 반환한 ID를 이 결정만으로 삭제하지도 않는다.

## 저장·호출·실패 처리

연결 설정 변경은 기존 관리자 권한, version, idempotency, 감사, Endpoint 보안 정책을 유지한다. `CUSTOM` 라우터의 Auto를 허용·저장할 때는 기존 호환 Adapter의 모델별 연결 시험을 적용한다. 시험 실패 시 연결·Key·허용 목록을 변경하지 않는다. 시험은 실제 사용료가 발생할 수 있으므로 화면에 비용 가능성을 명시하고 명시적 실행 때만 수행한다. 실제 질문에서는 선택한 Auto의 실제 ID를 `model`로 전달하고, Daon 자체의 새 라우팅 알고리즘이나 fallback을 만들지 않는다.

## 검증과 배포 경계

- migration: 기존 연결·Key·허용 목록·기본 모델 보존, 고정 두 라우터만 backfill, 다른 연결은 NULL.
- API/DB: 관리자만 라우터 설정 변경, 중복 Auto 방지, 조회 누락 시 logical Auto 유지, 새로고침·실패 시험 불변, 기존 모델 선택 회귀.
- 실행: OmniRoute `auto`, OpenRouter `openrouter/auto`, `CUSTOM` Media Bridge의 `auto`를 각 Adapter로 전달하는 fixture 검증. 실제 유료 Provider 시험은 별도 비용 승인 전 수행하지 않는다.
- 화면: Auto 선택·해제·재조회, 일반 Provider 비노출, 기존 Key 보존, same-origin BFF, 1920×1080 중심 반응형 확인.
- WSL/Oracle 배포와 기존 Media Bridge Server의 실제 설정 변경은 구현·로컬 검증 결과와 대상·영향을 먼저 보고하고 승인된 범위에서만 수행한다.

## 대안과 선택 이유

`provider_code`/표시 이름을 하드코딩하는 방법은 빠르지만 임의 이름의 `CUSTOM` 라우터를 안정적으로 구분하지 못한다. 별도 `is_router`와 모델 ID 컬럼 두 개를 두는 방법은 표현이 중복되어 불일치가 가능하다. nullable `auto_model_id` 하나가 라우터 여부와 실제 호출 ID를 함께 명시하므로 이번 Auto-only 범위에 가장 작다. 향후 `combo`나 다른 라우팅 기능이 필요하면 별도 승인·설계를 거친다.

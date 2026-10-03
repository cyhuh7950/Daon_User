# Provider 호환 프로토콜 및 수동 모델 등록 설계

상태: 신산님이 대화에서 방향 승인(2026-10-03). 이 문서의 검토·승인은 구현 착수 전에 별도로 받는다.

## 목적과 범위

관리자는 특정 회사 이름을 고르는 대신 통신 규격을 먼저 선택해 새 모델 연결을 등록한다. 이번 실제 지원 범위는 `OpenAI 호환 / Chat Completions`와 `Anthropic 호환 / Messages` 두 가지다. 두 방식 모두 모델 목록 조회가 가능하면 목록에서 고르고, 조회를 지원하지 않으면 모델 ID를 직접 입력한다. 어느 경우든 지정한 모델에 대한 실제 연결 시험이 성공한 뒤에만 연결을 사용 가능 상태로 저장한다.

기존 연결의 ID·Provider 코드·접근 방식·Key·허용 모델·Workspace 기본 모델은 변경하지 않는다. 기존 `Ollama Public`, UPSTAGE `solar-pro4` 단일 허용, 공용/비공용 Key 정책, 두 글자 약어와 삭제 보호를 회귀 기준으로 둔다. 새 호환 방식은 외부 HTTPS Endpoint와 Key를 사용하는 연결로 한정한다. 임베딩, Responses, 오디오, 이미지, RAG, 자동 상태 점검 스위치, 라이선스, ysna/Oracle 배포는 제외한다.

## 설계 선택

기존 `system_provider_connections.adapter_type`에 `openai_compatible` 또는 `anthropic_compatible`을 기록하고 새 연결의 `provider_code`는 `CUSTOM`으로 유지한다. `provider_name`은 관리자가 자유 입력한다. 새 DB 컬럼이나 기존 행을 다시 쓰는 migration은 만들지 않는다. 기존 이름별 Adapter는 그대로 두고, `CUSTOM`에만 두 호환 방식을 분기한다. 새 `provider_code`별 하드코딩 목록을 늘리는 방식은 사용자가 원하는 임의 Provider 등록을 해결하지 못하므로 선택하지 않는다.

관리자 화면의 신규 연결에서는 먼저 `호환 방식`, 다음 `API 유형`을 표시한다. API 유형은 선택한 방식의 이번 지원값 하나(`Chat Completions` 또는 `Messages`)만 활성화하며, 미지원 API 유형을 성공 가능한 선택지처럼 보여주지 않는다. 저장된 연결의 규격은 읽기 전용이며, 변경하려면 새 연결을 등록한다. 기존 연결을 다른 규격으로 암묵 변환하지 않는다.

## 관리자 등록 흐름

관리자는 연결 ID·Provider 표시 이름·연결 이름·고유 약어·Endpoint·공용 여부·모델 ID·필요한 Key를 입력한다. 모델 목록 조회는 선택 기능이다. 조회 실패·미지원이더라도 직접 입력으로 진행할 수 있지만, 잘못된 Key나 Endpoint를 조회 실패만으로 성공 처리하지 않는다. 모델 ID를 하나 이상 확정해야 연결 시험을 시작할 수 있다.

`연결 시험 및 저장`은 허용할 모델마다 짧은 비스트리밍 생성 요청을 최대 한 번 보내 규격별 HTTP 성공과 최소 응답 형식을 확인한 뒤 DB 저장을 수행한다. OpenAI 호환은 `POST /chat/completions`, Anthropic 호환은 `POST /messages`를 사용한다. 출력 토큰 한도는 16이고 테스트 전용 짧은 입력을 사용하며 자동 재시도·fallback은 하지 않는다. 2xx 응답이어도 OpenAI `choices[0].message.content` 또는 Anthropic `content`의 text 블록에서 비어 있지 않은 문자열을 확인하지 못하면 실패다. 이 시험은 실제 Provider 사용료가 발생할 수 있으므로 버튼 옆에 시험 대상 모델 수와 비용 가능성을 고지한다. 어느 모델이든 시험 실패·타임아웃·응답 형식 오류가 나면 연결·Key·허용 모델·검증 상태를 변경하지 않고 안전한 오류만 보여준다. 기존 연결 수정도 변경된 모델에 대해 같은 실패 불변성을 따른다.

공용 연결은 관리자 Key를 시험 후 암호화 저장한다. 비공용 연결은 관리자에게 일회성 시험 Key를 받아 시험에만 사용하고 저장하지 않는다. 비공용 연결의 사용자는 자기 Key를 등록할 때 동일 규격·허용 모델로 시험하고, 성공한 본인 Key만 암호화 저장한다. Key가 불필요한 기존 연결은 지금처럼 공용이며 이 신규 흐름으로 재분류하지 않는다. 관리자만 Endpoint·허용 모델·공용 여부를 바꿀 수 있다. 저장된 호환 방식은 관리자에게도 수정 불가다.

목록 조회가 가능한 경우에도 선택 모델의 실제 시험을 생략하지 않는다. 조회가 없는 경우에는 시험에 성공한 수동 모델 ID를 연결의 카탈로그와 관리자 허용 목록에 함께 기록한다. 조회된 다른 모델을 자동으로 허용하지 않는다. 사용 중인 Workspace 기본 모델을 허용 목록에서 제거하는 기존 차단 규칙은 유지한다. 기존 연결의 카탈로그/허용 목록은 새 규격 구현으로 재작성하지 않는다.

## 서버 실행과 보안

연결 시험, 개인 Key 검증, 실제 질문 실행은 모두 DB의 `adapter_type`으로 `CUSTOM` 규격을 선택한다. Workspace 모델 resolver가 규격을 실행 선택에 전달하며, 일반 대화와 근거 기반 질문이 같은 규격을 사용한다. OpenAI 호환은 기존 Chat Completions 본문/응답 처리를 유지한다. Anthropic 호환은 `anthropic-version`, Key 인증 헤더, 최상위 `system`, `messages`, `max_tokens` 및 `content` 텍스트 블록 응답을 사용한다. 근거 기반 질문은 기존 인용·증거 검증을 동일하게 적용하고, 일반 대화는 기존 일반 대화 검증을 적용한다. JSON 응답이 불완전하거나 근거 검증에 실패하면 성공 답변으로 저장하지 않는다.

외부 Endpoint는 현재 `CUSTOM`의 공개 HTTPS·주소 검증과 no-redirect·bounded timeout/response·egress 승인을 약화하지 않는다. 서버만 Provider를 호출하고 브라우저는 same-origin BFF를 사용한다. 입력 Key는 응답·URL·오류·로그·감사기록에 표시하지 않는다. 일회성 Key는 시험 이후 폐기한다. 기존 관리자 인증/step-up, version, idempotency, 권한 계약은 별도 승인 없이 변경하지 않는다.

## 검증과 완료 기준

- API 단위/계약: 두 규격의 조회 성공·조회 없음+수동 ID·시험 성공·인증 실패·타임아웃·형식 불량·중복 저장을 검증한다. 비공용 일회성 Key 비저장과 일반 사용자 설정 변경 거부를 검증한다.
- 실행: 동일 모델로 일반 대화와 근거 기반 질문을 규격별로 검증하고, 기존 OpenAI 호환·Ollama·Gateway·UPSTAGE 경로 및 인용 검증 회귀를 확인한다.
- 데이터: 기존 연결/Key/카탈로그/허용 모델/Workspace 기본 모델 개수와 식별자 보존을 배포 전후 비교한다. 실패 시험은 DB 불변이어야 한다. 새 schema migration이 없는 것을 확인한다.
- 화면: 모델 조회/수동 입력, 규격별 API 유형, 비용 고지, 성공·실패 상태, 관리자/일반 사용자 권한과 same-origin Network를 확인한다. 1920×1080·1440×900·430×844에서 확인한다.
- 환경: 로컬 테스트와 빌드 후 exact SHA로 WSL 개발 환경에만 검증한다. WSL의 기존 dirty checkout과 Secret을 덮어쓰지 않는다. 실제 Provider Key가 없어 외부 시험을 못 하면 fixture 통과와 실제 연결 미검증을 구분해 보고한다.

## 근거와 남는 제한

현재 코드의 `CUSTOM`은 OpenAI 호환으로 고정되고, 연결 시험·질문 실행은 `provider_code`로 분기한다. `adapter_type`은 이미 저장되지만 질문 실행까지 전달되지 않는다. 기존 비공용 `CUSTOM`에는 관리자 Key 없이 수동 모델을 안전하게 등록할 경로가 없다. 이 설계는 그 연결 경로를 확장하되 0050 migration과 기존 데이터는 보존한다.

Anthropic 호환 서버가 표준 Messages의 인증·본문·응답 형식을 따르지 않으면 시험 실패로 표시하며 임의 규격 자동 탐지는 하지 않는다. OpenAI 호환 서버도 Chat Completions 응답 형식을 따르지 않으면 같은 방식으로 실패한다. Provider별 비표준 확장은 후속 설계 대상이다.

참고: [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages/create), [Anthropic 모델 목록 API](https://platform.claude.com/docs/en/api/models/list), [OmniRoute 사용자 가이드](https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.51/docs/guides/USER_GUIDE.md). OmniRoute의 화면 개념만 참고했으며 소스 코드는 복사하지 않는다.

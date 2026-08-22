# 실제 데이터 기반 대화·Studio 수직 흐름 구현계획

1. 정본·Branch·HEAD·dirty 보호 범위와 제품 DB Fixture inventory를 기록한다.
2. Production entry의 Fixture import/default data 0과 E2E 고유 Workspace cleanup 계약을 RED→GREEN으로 고정한다.
3. Source/Knowledge/Conversation/Studio 병렬 로드를 영역별 reducer로 분리하고 오분류 오류를 RED→GREEN한다.
4. 질문 authoritative replay는 현재 Notebook Binding과 저장 Run Provider scope를 재검증하며, external 신규·replay 모두 current `EXTERNAL_LLM`과 exact effective Egress Policy를 도메인 write 전에 fail-close한다.
4.1 외부 authorizer→Provider→완료 저장 actual 경로에서 authorizer와 repository가 동일 canonical Run payload를 공유하고, 최초 immutable Run이 완전한 replay metadata와 Conversation FK를 보유하는지 검증한다.
4.2 동일 idempotency key 2-connection 최초 요청에서 durable decision creator만 Provider owner가 되고 follower는 bounded replay만 수행하는지 검증한다. owner 미완료 timeout은 same-key Provider 재호출0의 retryable fail-close, 새 key 재시도 가능 계약으로 고정한다.
4.3 동일 run/wire/frozen이지만 request fingerprint가 다른 동시 follower를 actual PostgreSQL에서 `IDEMPOTENCY_KEY_REUSED`로 차단하고, Provider 추가 호출·결과 반환·도메인 write가 0인지 검증한다.
4. 일반 대화 intent의 좁은 allowlist와 grounded 질의 기본값을 Domain TDD로 구현한다. 일반 대화만 Evidence 없이 선택 Provider를 호출하고 명시적으로 ungrounded 표시한다.
5. Provider selection→RunSnapshot→응답/Studio Output lineage와 Citation exact projection을 API·PostgreSQL transaction에서 검증한다.
6. same-origin BFF·Web·Desktop Native adapter와 3열 상태를 동일 DTO로 연결한다.
7. 기존 Policy API·Step-up·same-origin BFF를 재사용해 설정 화면에서 organization policy와 workspace policy를 별도 단계로 저장하고, 양쪽을 합성한 effective projection이 승인 범위와 exact 일치함을 확인한다. 비밀번호는 관리자 정책 저장 단계에서만 uncontrolled/ref 또는 함수 지역으로 사용하고 요청의 성공·실패 후 즉시 비운다. 일반·근거 질문과 Studio 사용은 유효 Session·권한·effective Policy를 매 요청 재검증하며 재인증 UI나 Step-up을 호출하지 않는다.
8. 대표 Provider 1개(`UPSTAGE | GROQ | MISTRAL`)를 선택해 실제 Source 업로드→처리→일반 대화→근거 질문→Citation→Studio 저장을 수행한다.
9. actual PostgreSQL/RLS, Browser 1920x1080, Windows 가능 범위, Network/console/secret scan, Fixture cleanup0을 Evidence로 결속한다.
10. API/Node/Rust/build/boundary 회귀, diff-check, staged0을 확인하고 독립 검토를 요청한다.

중단 조건은 공개 API·데이터·보안 계약의 새 변경, 실제 사용자 데이터 삭제 위험, 파괴적 외부 배포뿐이다. 계획 안의 오류는 원인을 진단하고 계속 진행한다.

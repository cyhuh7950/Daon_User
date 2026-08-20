# 실제 데이터 기반 대화·Studio 수직 흐름 구현계획

1. 정본·Branch·HEAD·dirty 보호 범위와 제품 DB Fixture inventory를 기록한다.
2. Production entry의 Fixture import/default data 0과 E2E 고유 Workspace cleanup 계약을 RED→GREEN으로 고정한다.
3. Source/Knowledge/Conversation/Studio 병렬 로드를 영역별 reducer로 분리하고 오분류 오류를 RED→GREEN한다.
4. 일반 대화 intent의 좁은 allowlist와 grounded 질의 기본값을 Domain TDD로 구현한다. 일반 대화만 Evidence 없이 선택 Provider를 호출하고 명시적으로 ungrounded 표시한다.
5. Provider selection→RunSnapshot→응답/Studio Output lineage와 Citation exact projection을 API·PostgreSQL transaction에서 검증한다.
6. same-origin BFF·Web·Desktop Native adapter와 3열 상태를 동일 DTO로 연결한다.
7. 기존 Policy API·Step-up·same-origin BFF를 재사용해 설정 화면에서 organization policy와 workspace policy를 별도 단계로 저장하고, 양쪽을 합성한 effective projection이 승인 범위와 exact 일치함을 확인한다. 비밀번호는 단계별 uncontrolled/ref 또는 함수 지역으로만 사용하고 요청의 성공·실패 후 즉시 비운다.
8. 대표 Provider 1개(`UPSTAGE | GROQ | MISTRAL`)를 선택해 실제 Source 업로드→처리→일반 대화→근거 질문→Citation→Studio 저장을 수행한다.
9. actual PostgreSQL/RLS, Browser 1920x1080, Windows 가능 범위, Network/console/secret scan, Fixture cleanup0을 Evidence로 결속한다.
10. API/Node/Rust/build/boundary 회귀, diff-check, staged0을 확인하고 독립 검토를 요청한다.

중단 조건은 공개 API·데이터·보안 계약의 새 변경, 실제 사용자 데이터 삭제 위험, 파괴적 외부 배포뿐이다. 계획 안의 오류는 원인을 진단하고 계속 진행한다.

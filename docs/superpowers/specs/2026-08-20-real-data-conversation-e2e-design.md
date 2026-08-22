# 실제 데이터 기반 대화·Studio 수직 흐름 설계

## 목적

사용자 화면에서 테스트 Fixture를 제거하고, 선택된 Notebook의 실제 Source 또는 Daon 지식을 바탕으로 대화·Citation·Studio 산출물 저장까지 연결한다. 일반 인사와 근거 기반 질문을 구분하되 Provider 자동 대체와 가짜 성공은 허용하지 않는다.

## 제품 계약

1. Production Web/Desktop entry는 Test Harness·Fixture Adapter를 import하거나 기본 데이터로 사용하지 않는다.
2. Source·Knowledge·Conversation·Studio 각 영역은 독립적으로 로드한다. Conversation 실패가 Source 실패로 표시되거나 기존 Source 목록을 지우면 안 된다.
3. `안녕`, `고마워`, 제품 사용 도움말처럼 제한된 비사실 대화는 현재 사용자가 선택한 Provider/Deployment를 실제 호출한다. 응답은 `일반 대화 · 근거 미사용`으로 표시하고 Citation은 0개다.
4. 사실·분석·요약 질문은 선택된 Notebook Context의 Daon Knowledge와 Raw Source Evidence를 검색한다. Evidence가 없으면 Provider 호출 0과 안전한 근거 부족 상태를 유지한다.
5. 근거 기반 응답은 Citation의 SourceVersion·EvidenceSpan·locator를 검증하고, 동일 Provider selection과 Knowledge Context가 RunSnapshot·Studio Output lineage에 유지되어야 한다.
6. 9개 Provider 설정·선택 구조는 유지한다. 기능 actual Gate는 `UPSTAGE | GROQ | MISTRAL` 중 가용한 대표 1개로 수행하고, 연결 확인은 Provider별 독립 상태로 표시한다. 자동 fallback은 0이다.
7. Browser는 same-origin BFF만 사용하며 내부 URL·Credential·Stack·SQLSTATE를 노출하지 않는다.
8. 실제 외부 Provider Gate 전에 organization·workspace 두 scope의 versioned Egress Policy가 모두 승인 범위와 일치해야 한다. 설정 화면은 두 scope를 별도 단계로 표시하고 각 저장마다 기존 Step-up을 사용한다. 한 단계 성공을 전체 effective 성공으로 표시하지 않으며, 현재 비밀번호는 요청 완료·실패 후 즉시 비운다.
9. Step-up은 organization·workspace 정책 변경, License 등 관리자 설정 작업에서만 사용한다. 로그인 Session이 유효하고 선택 Workspace의 `EXTERNAL_LLM` 권한과 effective Egress Policy가 허용이면 일반·근거 질문과 Studio 사용 중 비밀번호를 다시 요구하지 않는다. 서버는 사용 시점마다 Session·Workspace·Notebook scope, Provider·목적지·분류·마스킹·redaction을 재검증한다.
10. 완료된 질문의 동일 Idempotency replay도 저장 결과에 대한 현재 접근이다. 서버는 저장 Run의 canonical Provider 종류와 외부전송 범위를 사용해 현재 Notebook 선택 Binding을 다시 확인하고, 외부 Provider 결과이면 현재 `EXTERNAL_LLM` 권한과 effective Policy의 Provider·목적지·payload 크기·`internal` 분류·마스킹·redaction을 모두 재검증한 뒤에만 저장 결과를 반환한다. 새 외부 질문도 같은 exact 정책 검증을 Provider·Run·Egress·Audit 도메인 기록 전에 수행한다.
11. 외부전송 authorizer와 완료 저장은 동일한 canonical Run payload 생성기를 사용한다. Provider 호출 전 authorizer가 결정론적 Conversation과 `request_fingerprint`·`provider_kind`·`egress_scope`·`conversation_id`를 포함한 완전한 immutable Run을 최초 생성하고, 완료 저장은 동일 payload를 idempotent하게 재사용한다. 불완전 선삽입이나 충돌 무시로 replay metadata를 유실하지 않는다.
12. 동일 idempotency key의 동시 최초 외부 요청은 authorizer의 PostgreSQL advisory transaction lock과 durable egress decision으로 단일 Provider owner를 정한다. 신규 decision 생성자만 Provider를 호출하고 follower는 bounded completed replay를 기다린다. owner 미완료·장애 시 같은 key는 Provider를 탈취하지 않고 retryable fail-close하며, 새 idempotency key만 새 Run으로 재시도한다.
13. advisory lock 뒤 existing Run은 frozen scope만이 아니라 question·context mode·Source IDs·request fingerprint를 포함한 완전한 canonical payload가 현재 요청과 exact 일치해야 follower가 된다. 불일치는 `IDEMPOTENCY_KEY_REUSED`로 write0 차단하고, 완료 follower도 fingerprint-aware authoritative replay로만 결과를 반환한다.

## Fixture 정리 계약

- 사용자 화면에 노출되는 runtime DB/Object Storage 행만 정리 대상이다.
- 테스트 코드, 전용 Harness, 검증 Evidence는 삭제하지 않는다.
- 삭제 전 Tenant·Workspace·Source ID·filename·종속 행 수를 read-only로 고정한다.
- 현재 WSL 제품 DB에서 Source 0건, fixture Source 0건이면 제품 데이터 삭제는 수행하지 않고 `already absent`로 기록한다.
- 향후 E2E Fixture는 고유 Test Workspace와 bounded cleanup을 필수로 하며 production Notebook 목록에 투영하지 않는다.

## 실제 완료 Gate

1. 빈 제품 DB에서 Fixture 이름·가짜 Source·가짜 Output 0.
2. 실제 업로드 Source가 Source 목록에 나타나고 처리 완료 상태가 된다.
3. `안녕`은 선택 Provider가 실제 호출되어 자연어 응답과 `근거 미사용` 표시를 반환한다.
4. 실제 Source 질문은 Provider 호출, Citation, RunSnapshot을 생성한다.
5. 동일 Context로 Studio 산출물을 만들고 저장 Library에서 다시 읽는다.
6. 오류 상태는 Source·Conversation·Studio별로 분리되며 재시도가 정상 영역을 지우지 않는다.
7. 1920x1080 실제 Browser/Windows, same-origin Network, console 오류 0, Fixture cleanup 0 잔류를 검증한다.
8. organization·workspace policy 적용 후 effective projection이 명시 승인값과 exact 일치할 때만 Provider 호출을 시작한다.

## 제외 범위

- 모든 Provider에 동일 기능 E2E 반복
- Ollama 모델 설치·삭제 또는 이번 대표 기능 시험 강제
- 근거 없는 사실 답변 허용
- 테스트 코드·Evidence 일괄 삭제
- 공개 Provider 자동 fallback

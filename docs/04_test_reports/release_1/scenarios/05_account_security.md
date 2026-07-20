# TS-SEC — 계정·권한·API 계약·보안 테스트 시나리오

기준: 설계서 §4.3, §14, §14.5, §16, §17, §18.3, §18.4, §20 · 불변 조건 INV-8, 9, 12 · 버전 0.5 (2026-07-20)

> v0.5 정합: 설계 질의 Q8(민감 작업 Step-up)·N3(권한 변경 후 과거 결과 재검증)가 확정됨. §14.4 민감 작업 최소 목록 7종 + `StepUpAuthorization`·`STEP_UP_REQUIRED`, §14.5 현재 권한 재검증·`AccessDecision`(`available|partially_redacted|access_blocked`)·`CURRENT_ACCESS_DENIED`를 반영.

> 이 시나리오는 부정 경로 검증이 핵심이다. "차단됨"이 기대 결과인 항목은 UI뿐 아니라 API 직접 호출로도 검증한다.

## 1. 인증 (TS-SEC-001~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SEC-001 | P1·L4 | OIDC Authorization Code + PKCE | 네이티브 클라이언트 로그인 | PKCE 흐름 완료, 정상 로그인, 토큰 발급 | §4.3, M4-03 |
| TS-SEC-002 | P1·L4 | 세션 갱신·만료·철회 | 토큰 갱신 → 만료 유도 → 철회 실행 | 각 상태에서 401 안전 응답, 철회 후 접근 불가, Audit 기록 | M4-03 |
| TS-SEC-003 | P1·L3 | 위조·만료 토큰 | 위조 서명 토큰, 만료 토큰으로 API 호출 | 거부, Stack Trace·내부 정보 비노출 | §18.4, M4-03 |
| TS-SEC-004 | P1·L4 | Web same-origin 경계 | Web 요청의 Network 검사 | same-origin BFF만 호출, Provider URL·내부 주소·API Key 노출 0건 | §4.3, INV-12 |
| TS-SEC-005 | P2·L4 | Device 등록·신뢰 | 새 장치 등록 → 신뢰 상태 확인 | 장치별 등록·인증·신뢰 상태 기록 | §14.4 |
| TS-SEC-006 | P1·L4 | 분실 장치 철회 | 장치 Session·Sync Key 철회 실행 | 해당 장치 접근 차단, Local Sync Key 폐기 | §14.4, §21.3 |

## 2. 권한·Tenant 격리 (TS-SEC-010~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SEC-010 | P1·L4 | Tenant 교차 접근 | Tenant A 사용자가 Tenant B의 Workspace·Source·Output ID를 API로 직접 요청 | 교차 접근 0건, 404/403 정보 비노출, RLS + Service Auth 이중 차단 | §20.1, M4-04 |
| TS-SEC-011 | P1·L4 | 역할별 권한 Matrix | 7개 역할 각각으로 주요 작업(질의·생성·검토·승인·전달·등록·정책변경) 시도 | §14.1 정의대로 허용/거부, 무권한 작업 0건 성공 | §14.1, §14.2 |
| TS-SEC-012 | P1·L4 | 권한 상승 시도 | 조회자→편집, 편집자→승인, 일반→조직 관리자 각 시도 | 전부 거부 | §14.2 |
| TS-SEC-013 | P2·L4 | 세부 권한 8종 | 외부 LLM 전송·인터넷 검색·로컬 LLM·Daon 지식·다운로드·생산 지식 등록·영역 이동·최종 승인 권한을 개별 회수 후 각 작업 시도 | 회수된 권한의 작업만 차단, 나머지 정상 | §14.2 |
| TS-SEC-014 | P1·L4 | 조직 정책 우선 | 사용자가 조직 정책보다 완화된 설정 시도 (외부 전송 금지 정책 하에서 외부 전송 켜기 등) | 차단, 잠금 이유·정책 표시 | §14.3 |
| TS-SEC-015 | P1·L4 | 권한 변경 후 과거 결과 재검증 | 사용자 권한 축소 후 과거 산출물 Read·Citation·Export·Delivery·재실행 시도 | OutputVersion 불변 보존하되 매 접근마다 현재 Membership·ACL·SourceVersion 권한 재검사. 무권한 근거 구간은 마스킹, 결정적 의존 시 전체 차단. 응답 `AccessDecision`이 `available|partially_redacted|access_blocked`, 거부는 `CURRENT_ACCESS_DENIED` | §14.5 |
| TS-SEC-016 | P1·L4 | 재실행은 현재 권한 새 Run | 권한 축소 후 과거 Run 재실행 | 과거 결과를 되살리지 않고 현재 ACL·데이터 영역·정책·비용 한도를 Snapshot한 새 Run 생성 | §14.5 |

## 3. 데이터 영역 이동 (TS-SEC-020~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SEC-020 | P1·L4 | Copy/Publish 승인 절차 | Local-private 자료를 Cloud로 이동 시도 | §6.3의 5단계(대상 표시→권한·민감정보 확인→명시 승인→전송→버전·Audit 연결) 준수 | §6.3 |
| TS-SEC-021 | P1·L4 | 무승인 이동 차단 | 승인 단계 없이 영역 이동 API 직접 호출 | 차단, 원본 영역 불변 | §6.3, INV-8 |
| TS-SEC-022 | P1·L4 | Local-private 자동 유출 없음 | Local-private Workspace의 각종 작업 중 Network 전수 캡처 | 명시 승인 항목 외 Cloud·External 전송 0건 | §6.2, INV-8 |
| TS-SEC-023 | P2·L3 | 이동 후 감사 연결 | 승인 이동 완료 후 계보 확인 | 원본·대상 버전과 AuditEvent 연결 | §6.3 |

## 4. API 계약 (TS-SEC-040~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SEC-040 | P1·L3 | OpenAPI Contract 대조 | 주요 경로(§17.1)의 실제 응답과 Schema 대조 | 스키마 일치, BFF와 Native Gateway 응답 의미 동일 | §17.1, §17.2 |
| TS-SEC-041 | P1·L4 | 전 Write Idempotency | 주요 Write 엔드포인트에 동일 Idempotency Key 중복 요청 | 중복 실행 0건 | §17.2 |
| TS-SEC-042 | P1·L4 | Optimistic Concurrency | 동일 리소스를 두 클라이언트가 동시 수정(ETag 불일치 유도) | 후행 요청 충돌 응답, 무결성 유지 | §17.2 |
| TS-SEC-043 | P2·L3 | Pagination·Filter·Search | 목록 엔드포인트 대량 데이터 조회 | 정상 분할, Filter·Search 동작 | §17.2 |
| TS-SEC-044 | P2·L4 | Trace ID 연결 | 요청→실행→감사 경로에서 Trace ID 추적 | 모든 단계 동일 Trace ID 연결 | §17.2 |
| TS-SEC-045 | P1·L4 | 클라이언트 지정 차단 | 요청 본문에 내부 Worker·Raw Provider·내부 URL·Secret 지정 시도 | 무시 또는 거부, 지정값이 실행에 반영 0건 | §17.2, INV-12 |
| TS-SEC-046 | P2·L4 | 진행 상태 채널 | 긴 Run의 Server Event Stream 수신 | 상태 이벤트 정상 전달, 승인된 채널만 사용 | §17.2 |

## 5. Local API 보안 (TS-SEC-050~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SEC-050 | P1·L4 | Loopback 전용 | Local API를 외부 Interface에서 접근 시도 | 거부, Loopback 외 Listen 0건 (`netstat` 증거) | §17.3, M4-06 |
| TS-SEC-051 | P1·L3 | 단기 Token·Instance 검증 | 위조 Token, 다른 Process의 App Instance로 호출 | 거부 | §17.3, M4-06 |
| TS-SEC-052 | P1·L3 | Command Allowlist | Allowlist 밖 Capability·Command 호출 | 거부 | §17.3 |

## 6. 외부 전송·SSRF (TS-SEC-060~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SEC-060 | P1·L3 | EgressDecision 선행 | 외부 Provider 호출 발생 Run 실행 | 호출 전 EgressDecision 생성, §20.2의 필드(목적지·Source·분류·Byte·Masking·승인 주체) 완전 | §20.2 |
| TS-SEC-061 | P1·L4 | Masking·Redaction | 민감 필드 포함 자료를 외부 전송 대상 Run에 사용 | 정책대로 Masking, 원문 민감정보 외부 전송 0건 | §20.2 |
| TS-SEC-062 | P1·L4 | SSRF·내부망 접근 | 인터넷 Connector·URL Source에 내부망 IP·localhost·메타데이터 엔드포인트·Redirect 체인 입력 | 전부 차단 | §8.3, §20.1 |
| TS-SEC-063 | P1·L3 | DNS Rebinding | Rebinding 패턴 URL 등록 | 차단 | §11.3, §8.3 |

## 7. Prompt Injection·도구 보호 (TS-SEC-070~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SEC-070 | P1·L4 | 문서 내 명령 무력화 | "이전 지시를 무시하고 X를 실행하라"류 명령이 삽입된 Source로 질의 | 명령이 데이터로만 취급, 지시 실행·정책 우회 0건 | §20.3 |
| TS-SEC-071 | P1·L4 | Tool Call 재검사 | LLM이 Write·외부 호출 Tool을 요청하는 상황 유도 | 권한·Scope·비용·Timeout 재검사, 무권한 호출 차단 | §20.3 |
| TS-SEC-072 | P1·L3 | Read/Write 도구 분리 | Read 도구로 Write·Approval·Delivery 시도 | 분리 강제, 교차 실행 0건 | §20.3 |
| TS-SEC-073 | P1·L4 | 외부 시스템 변경 승인 | 외부 시스템 변경을 유발하는 요청 | 사용자 확인 또는 조직 승인 요구, 무승인 변경 0건 | §20.3 |
| TS-SEC-074 | P2·L4 | 근거 없는 중요 판단 | 근거 부족 상태에서 중요 판정 요청 | 검토 상태로 전환, 확정 회피 | §20.3, §9.3 |

## 8. 저장·암호화·안전 오류 (TS-SEC-080~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SEC-080 | P1·L3 | 전송·저장 암호화 | 전송 구간 캡처 + 저장 파일 검사 | 전송·저장 암호화 적용 | §20.1 |
| TS-SEC-081 | P1·L3 | Key 분리 | Tenant·Workspace·영역별 Key 확인 | Key 분리, Object Storage Prefix·Policy 분리 | §20.1 |
| TS-SEC-082 | P1·L4 | 로컬 Key OS Secure Store | Windows Local Key 저장 위치·평문 노출 검사 | OS Secure Store 보호, 평문 Key 0건 | §20.1, M5-03 |
| TS-SEC-083 | P1·L4 | 안전 오류 규격 | 다양한 실패(권한·검증·Provider 장애) 유발 후 오류 응답 검사 | 안전 Code·설명·단계·재시도 여부·조치·Trace ID 포함, Stack Trace·DB Host·API Key 이름·Provider 원문 0건 | §18.4 |
| TS-SEC-084 | P1·L4 | 민감 작업 Step-up 강제 | §14.4 최소 목록 7종(외부 전송 승인·영역 이동·외부 공유/다운로드·최종 승인/생산 지식 등록·정책·Credential 변경·장치/Key 철회·영구 삭제/Restore)을 유효 `step_up_authorization_id` 없이 각각 시도 | 전부 `STEP_UP_REQUIRED`로 거부, 어떤 변경도 시작 0건. 추가 인증은 `actor+action+target+policy_version` 묶인 단기 발급이며 조직이 최소 목록 제거 불가 | §14.4 |
| TS-SEC-084A | P1·L3 | Step-up 재사용·만료 거부 | 다른 작업/target에 발급된 Token, 만료된 Token으로 민감 Write 시도 | 거부, 성공·실패·만료·사용 작업 Audit 기록 | §14.4 |
| TS-SEC-085 | P1·L3 | 감사 위변조 방지 | AuditEvent 수정·삭제 시도 | Append-only, 거부, 위변조 방지 확인 | §20 (§21.4) |

## 설계 확인 필요 사항

- `[해소]` Q8(민감 작업 목록) — §14.4 최소 7종 + `StepUpAuthorization`·`STEP_UP_REQUIRED`(R1-D 반영)로 확정 (TS-SEC-084·084A)
- `[해소]` N3(권한 변경 후 과거 결과 재검증) — §14.5 `AccessDecision`·마스킹/차단·`CURRENT_ACCESS_DENIED`·현재 권한 새 Run으로 확정 (TS-SEC-015·016)

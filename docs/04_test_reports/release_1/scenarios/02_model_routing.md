# TS-MDL — 모델 선택·Routing·Fallback·관리형 로컬 LLM 테스트 시나리오

기준: 설계서 §10, §11, §16.1 · 불변 조건 INV-9, 10, 11, 12, 15 · 버전 0.5 (2026-07-20)

> v0.5 정합: (1) §10.5·INV-15의 Vision/LLM Parser-only 강등 금지와 `NO_AVAILABLE_UNDERSTANDING_MODEL`. (2) 설계 질의 Q3 확정 — 비용 한도 종료는 `policy_blocked/COST_LIMIT_EXCEEDED`, 동일 Frozen Context 자동 재시도 금지, 정책 변경 후 새 Run(R1-D015). Source 이해 모델 Fallback 상세는 `03_source_evidence.md`(TS-SRC-016·017)가 담당한다.

전제 Fixture: Local(장치), Internal(사내), External(외부 API) 각 1개 이상의 승인된 ModelDeployment와, 승인되지 않은 Deployment 1개를 등록한다. `[M0: R1-D006 Allowlist 확정 후 실제 모델 지정]`

## 1. 선택 Mode (TS-MDL-001~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-MDL-001 | P2·L4 | `auto` 기본 동작 | `auto` 선택 후 질의, RoutingDecision·Network 확인 | 승인된 RoutingPolicyVersion 안에서 선택, 선택 이유 기록, UI 표시 모델 = 실제 호출 모델 | §10.3, §10.4 |
| TS-MDL-002 | P1·L4 | `local_only(device_only)` | 해당 범위 선택 후 질의, 패킷 캡처 병행 | 이 장치의 Local Deployment만 후보, 외부·사내 호출 0건 | §10.3 |
| TS-MDL-003 | P1·L4 | `local_only(private_org_allowed)` | 해당 범위 선택 후 질의 | Local + 허용된 사내 LLM만 후보, External 호출 0건 | §10.3 |
| TS-MDL-004 | P2·L4 | `pinned` 직접 선택 | 허용된 Deployment를 직접 선택 후 질의 | 선택 모델로만 실행, RunResult의 최종 모델 일치 | §10.3 |
| TS-MDL-005 | P1·L3 | 불투명 ID 계약 | 클라이언트→서버 요청 본문 캡처 | Raw Provider Code·URL·Secret 없음, 권한 검증 가능한 불투명 ID만 전송 | §10.3, INV-12 |
| TS-MDL-006 | P1·L4 | 직접 선택의 정책 우회 불가 | 승인되지 않은 Deployment ID를 API로 직접 지정 | `policy_blocked`, 실행 0건 | §10.3, INV-9 |

## 2. Hard Filter·Readiness·정렬 (TS-MDL-010~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-MDL-010 | P1·L3 | Hard Filter 5항 개별 검증 | 소유권 밖·Local-only 위반·역할 불일치·라이선스 미허용·Residency 위반 후보를 각각 1개씩 포함시켜 질의 | 각 후보가 정책 제외 코드와 함께 탈락, RoutingDecision에 제외 사유 기록 | §10.4 |
| TS-MDL-011 | P2·L3 | Readiness Filter 4항 | 정책상 허용이지만 Node Offline·Digest 불일치·Secret 불가·Circuit Open인 후보 각각 준비 | Runtime 제외 코드로 탈락, 정책 제외와 코드가 구분됨 | §10.4 |
| TS-MDL-012 | P2·L3 | 결정론적 정렬 재현성 | 동일 조건에서 동일 질의 3회 실행 | 세 번 모두 동일한 후보 정렬·동일 선택(부하 요소 변화 없다는 전제), 정렬 순서가 Snapshot에 기록 | §10.4, §16.1 |
| TS-MDL-013 | P2·L3 | RoutingContext 고정 시점 | Run 시작 직후 정책·모델 설정 변경 | 진행 중 Run은 시작 시점 Context 유지, 변경은 다음 Run부터 | §10.4, §16.1 |
| TS-MDL-014 | P3·L3 | 역할별 라우팅 분리 | text·vision·speech_to_text·embedding·reranker가 모두 쓰이는 요청 실행 | 역할별로 별도 후보·선택이 이루어지고 각각 원장에 기록 | §10.1, §10.6 |

## 3. Fallback과 종료 상태 (TS-MDL-020~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-MDL-020 | P1·L4 | 정책 후보 0 | 모든 후보가 Hard Filter에서 탈락하는 조건 구성 | `policy_blocked` + 정책·권한·외부 전송 원인 Code `[API]` | §10.5 |
| TS-MDL-021 | P1·L4 | `auto` Runtime 후보 0 | 정책 후보는 있으나 전부 Offline/장애 | 재시도 가능 `failed` + `NO_AVAILABLE_DEPLOYMENT`. 단 문서 의미 이해 역할은 `NO_AVAILABLE_UNDERSTANDING_MODEL` | §10.5 |
| TS-MDL-022 | P1·L4 | `auto` 정상 Fallback | 1순위 후보에 Timeout 주입 | 같은 PolicyVersion·역할·데이터 영역 안의 다음 후보 자동 시도, 두 ModelAttempt 기록 | §10.5 |
| TS-MDL-023 | P1·L4 | 전환 금지 사유 | 1순위 후보에 인증 오류/Policy Block/잘못된 요청/외부 전송 거부를 각각 주입 | 4건 모두 다른 후보로 전환하지 않음. 인증 오류·잘못된 요청은 재시도 불가 `failed` | §10.5, INV-10 |
| TS-MDL-024 | P1·L4 | `pinned` 장애 시 waiting_user | pinned 모델에 Offline/Capacity 문제 주입 | 무단 모델 변경 0건, `waiting_user` 전환, 사용자에게 재시도 또는 허용된 다른 모델 선택지 제안(§10.5) | §10.5, §21.2 |
| TS-MDL-025 | P1·L4 | Local-private → External 금지 | Local-private Workspace에서 Local 후보 전체 장애 | External로 자동 전환 0건 (패킷 캡처로 입증) | §10.5, INV-8, 10 |
| TS-MDL-026 | P1·L4 | Stream 중단 후 이어쓰기 금지 | Stream 출력 중 모델 장애 주입 | 다른 모델이 이어쓰지 않음, Run은 실패 또는 재시작으로 처리 | §10.5 |
| TS-MDL-027 | P2·L3 | 자동 Attempt와 전환 제안 구분 | `auto` Fallback 발생 Run과 `pinned` 대안 제안 Run 실행 | 실행 결정 원장에서 자동 Attempt와 사용자 표시 제안이 구분 기록 | §10.5, §10.6 |
| TS-MDL-028 | P2·L3 | Embedding 변경 = 새 IndexVersion | Embedding 모델 버전 변경 후 재색인 | 기존 IndexVersion 불변, 새 IndexVersion 생성, 진행 중 검색은 기존 버전 사용 | §10.5 |
| TS-MDL-029 | P1·L4 | Vision/LLM 실패 시 Parser-only 강등 금지 | 문서 이해 Vision/LLM 후보 장애 상태에서 문서 처리 | 같은 정책·역할·영역의 다른 Vision/LLM 후보로만 Fallback. 승인 후보 0이면 ProcessingRun `policy_blocked`(정책) 또는 `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`(Runtime), Parser/OCR-only `ready` 0건 (상세 TS-SRC-016·017) | §10.5, §8.2, INV-15 |
| TS-MDL-030 | P1·L4 | 비용 한도 도달 종료 | 실행 중 누적/예상 비용이 한도 도달하도록 유도 `[M0: 한도 값은 R1-D010]` | `policy_blocked/COST_LIMIT_EXCEEDED` 종료, 동일 Frozen Context 자동 재시도 0건, 미완성 출력 전달 0건. 한도·통화·누적/예상 비용·차단 시점을 RoutingDecision·RunResult에 기록 | §10.4, §10.5, §18.2 |
| TS-MDL-031 | P2·L4 | 비용 한도 변경 후 새 Run | TS-MDL-030 차단 후 권한 사용자가 한도·정책 상향 → 재실행 | 기존 Run은 불변, 현재 권한·정책으로 새 Run 생성, 이전 차단이 자동 이어지지 않음 | §10.5 |
| TS-MDL-032 | P2·L3 | Preflight 비용 차단 | 다음 Attempt가 한도를 확실히 초과하는 상태에서 실행 | 새 호출을 시작조차 하지 않고 `COST_LIMIT_EXCEEDED`, RunSnapshot에 한도·통화·정책 버전 기록 | §10.4 |

## 4. 실행 결정 원장·Snapshot (TS-MDL-040~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-MDL-040 | P1·L3 | 원장 필수 필드 완전성 | 정상·Fallback·차단 Run 각 1건의 원장 검사 | §10.6의 12개 항목(Mode·후보·탈락 이유·Digest·Attempt·전송 범위·사용량 등) 전부 존재 | §10.6 |
| TS-MDL-041 | P1·L3 | RunSnapshot 불변성 | 완료된 Run의 Snapshot을 API로 수정 시도 | 거부. 시도가 Audit에 기록 | §16.1, INV-11 |
| TS-MDL-042 | P2·L4 | UI-Route-Network-Audit 4자 일치 | 각 Mode로 1회씩 실행하며 UI 표시, RoutingDecision, Network 목적지, AuditEvent 대조 | 4자 완전 일치. 불일치 1건이라도 있으면 S1 결함 | §10.6, M6 Exit |

## 5. 관리형 로컬 LLM (TS-MDL-050~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-MDL-050 | P2·L4 | 하드웨어 진단·추천 | 진단 화면 실행 `[M2]` | CPU·GPU·메모리·디스크 표시, 실행 가능 모델만 추천, 용량·예상 메모리·라이선스 표시 | §11.1 |
| TS-MDL-051 | P1·L4 | 다운로드 무결성 검증 | 정상 다운로드 1회 + 변조된 Artifact 주입 1회 | 정상: Digest·서명 확인 후 설치. 변조: `verifying`에서 실패, 설치 진행 0건 | §11.1 |
| TS-MDL-052 | P2·L4 | 설치→시험→Update→Rollback→삭제 전체 수명주기 | Artifact 상태 9종을 순회하는 수명주기 실행 | 각 상태가 §11.2 정의대로 전이, 사용자 CLI·Python 실행 0건 | §11.1, §11.2, INV-13 |
| TS-MDL-053 | P2·L4 | Update 실패 Rollback | Update 중 장애 주입 | `rollback` 상태를 거쳐 이전 버전으로 복귀, 이전 버전으로 정상 질의 가능 | §11.1, §11.2 |
| TS-MDL-054 | P2·L3 | 기존 런타임 표준 등록 | 기존 로컬 런타임을 Provider Adapter로 등록 | 표준 계약으로만 연결, 정상·권한 확인된 Deployment만 사용자에게 표시 | §11.3 |
| TS-MDL-055 | P1·L3 | 임의 LAN URL 차단 | 관리 승인 없는 LAN URL 등록 시도 + DNS Rebinding 패턴 URL | 거부. URL 검증·SSRF 방어 동작 | §11.3 |

## 6. Local Node·Device (TS-MDL-060~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-MDL-060 | P1·L4 | Pairing과 Outbound-only | 장치 Pairing 후 Node의 Listen 포트 검사 | 공개 Inbound Port 0건, Node가 여는 Outbound 연결만 존재 | §11.4 |
| TS-MDL-061 | P2·L3 | 인증서 회전 | 단기 인증서 만료 시점 경과 | 자동 회전, 서비스 중단 없음, 회전 이력 기록 | §11.4 |
| TS-MDL-062 | P1·L4 | Remote Revoke | 장치 폐기 실행 → 폐기된 Node로 접근 시도 | Key 무효화, 세션 철회, 접근 거부, Local Sync Key 폐기 | §11.4, §21.3 |
| TS-MDL-063 | P2·L4 | Web·모바일의 Relay 경유 접근 | Web에서 Local Node 자원 접근 | BFF/Gateway 인가 후 Relay 경유만 가능, 직접 접근 경로 없음 | §11.4 |
| TS-MDL-064 | P2·L3 | Node 상태 보고 | Heartbeat 중단 유도 | Node 상태 `degraded`/`offline` 전이, 해당 Node의 Deployment가 후보에서 제외 | §11.2, §11.4 |

## 설계 확인 필요 사항

- `[해소]` Q3(비용 한도 종료 상태) — §10.5 `policy_blocked/COST_LIMIT_EXCEEDED`·동일 Frozen Context 자동 재시도 금지·정책 변경 후 새 Run(R1-D015)으로 확정 (TS-MDL-030~032). 한도 값 자체는 M0(R1-D010).
- `[해소]` Q4(`waiting_user` 선택지 범위) — §10.5 "재시도 또는 다른 모델 선택"으로 확정 (TS-MDL-024)

# TS-KNW — 지식 유형·권위·가중치·충돌·RuleSet 테스트 시나리오

기준: 설계서 §5.4, §7, §8.4~8.6, §9 · 불변 조건 INV-1, 3, 4, 5, 6, 7 · 버전 0.5 (2026-07-20)

> v0.5 정합: 설계 질의 Q1(중요 충돌 판정)·Q2(가중치 척도)가 개정 §7.3·§7.4로 확정됨. 가중치 `0.5~2.0`·단위 `0.1`·기본 `1.0`·최근접 단일 계층(R1-D014), 충돌 `informational|material|critical`·ConflictPolicyVersion 자동 판정(R1-D013)을 시나리오에 반영.

## 1. 지식 유형과 범위 (TS-KNW-001~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-KNW-001 | P2·L4 | 다섯 지식 유형 개별 활성화 | 지식 패널에서 5개 유형을 하나씩만 활성화하고 각각 질의 `[M2]` | 활성 유형의 Source만 검색에 포함, RunSnapshot의 KnowledgeScope와 일치 | §5.4, §7.1 |
| TS-KNW-002 | P2·L4 | 전체 유형 비활성 상태 질의 | 모든 문서성 지식을 끄고 질의 | LLM 일반지식만으로 답변, `LLM 자체 지식` 표시, 문서 인용 0건 | §7.1, §8.5 |
| TS-KNW-003 | P2·L3 | Workspace 기본값과 요청별 임시값 | Workspace 기본 범위 설정 → 단일 요청에서 임시 범위 변경 → 후속 요청 | 임시값은 해당 Run에만 적용, 후속 요청은 기본값 복귀, 두 Run의 Snapshot이 각각의 범위 기록 | §5.4 |
| TS-KNW-004 | P2·L3 | Source 제외는 활성화 설정으로 | 특정 Source를 검색에서 빼려는 사용자 흐름 수행 `[M2]` | 가중치 0이 아니라 비활성화 설정으로 처리, 비활성 Source는 후보에서 완전 제외 | §7.3 |
| TS-KNW-005 | P2·L4 | 범위 Snapshot 재현성 | 동일 질의를 범위 변경 전후 각 1회 실행 | 과거 Run의 답변·인용은 과거 Snapshot 기준 유지, 새 Run만 새 범위 반영 | §16.1 |

## 2. 권위 순서와 포함 슬롯 (TS-KNW-010~)

전제 Fixture: 동일 주제에 대해 서로 다른 내용을 가진 Daon 승인 지식, 사용자 파일, 인터넷 Snapshot을 준비한다.

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-KNW-010 | P1·L4 | 권위 역전 시도 — 가중치 최대 | 하위 tier(사용자 파일)에 최대 가중치, Daon 승인 지식과 충돌하는 내용으로 질의 | 최종 판단은 Daon 승인 지식 기준, 사용자 파일 내용은 `충돌·대안` 표시. 가중치가 tier를 못 넘음 | §7.2, §7.3, INV-4 |
| TS-KNW-011 | P1·L4 | Daon 승인 지식 최소 포함 슬롯 | Daon 지식 활성 + 관련성 있는 질의, 하위 tier 후보가 다수인 상황 | 관련성 기준을 충족한 Daon 지식이 필수 슬롯에 포함, 하위 후보 때문에 탈락하지 않음. RunSnapshot에 슬롯 구성 기록 | §7.3, §9.2 |
| TS-KNW-012 | P2·L3 | 권위 Boost는 등급 표현값 | Boost 값이 적용된 상태에서 tier 간 점수 비교 로그 확인 | 서로 다른 tier가 하나의 곱셈 점수로 경쟁하지 않음(등급별 독립 검색 후 권위 우선 병합) | §7.3 |
| TS-KNW-013 | P2·L4 | 등급별 minimum relevance | 관련성이 낮은 Daon 지식만 있는 주제로 질의 | 관련성 미달 Daon 지식은 억지로 포함되지 않고, 근거 상태가 `부분 근거` 또는 `근거 부족`으로 표시 | §7.3, §9.3 |
| TS-KNW-014 | P2·L4 | Daon 권위 Boost 최소값 잠금 | 조직 정책으로 Boost 최소값 잠금 → 사용자가 하향 시도 `[M2]` | 변경 불가, 잠금 이유 표시 | §5.4, §14.3 |
| TS-KNW-015 | P3·L3 | 인터넷 지식 tier 내 평가 | 게시 시각이 다른 두 인터넷 Snapshot으로 질의 | 같은 tier 안에서 출처 품질·최신성이 순위에 반영 | §7.2, §9.2 |

## 3. 사용자 가중치와 Clamp (TS-KNW-020~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-KNW-020 | P2·L4 | tier 내 가중치 반영 | 같은 tier의 Source 2개에 서로 다른 가중치 설정 후 질의 | within_tier_score 순서에 가중치 반영(상위 가중치 Source가 우선 인용) | §7.3 |
| TS-KNW-021 | P2·L3 | 유형·그룹·개별 3단계 가중치 | 유형·그룹·개별 Source 가중치를 함께 설정 | `개별 Source > 그룹 > 유형 > 기본값` 중 가장 구체적인 값 하나만 적용, 계층 값 곱셈 0건, Snapshot에 적용 계층·값 기록 | §7.3 |
| TS-KNW-022 | P1·L4 | 관리자 Clamp | 조직 정책 min/max 범위 밖 가중치를 UI·API 각각으로 설정 시도 | Clamp 적용, Snapshot에 요청값이 아닌 Clamp 결과 기록, API 직접 호출도 동일 | §7.3, §14.3 |
| TS-KNW-023 | P1·L3 | 강제 지식 잠금 | 조직 관리자가 강제 포함 지식 설정 → 일반 사용자가 제외 시도 | 제외 불가, 잠금 이유 표시 | §7.3, §14.3 |
| TS-KNW-024 | P3·L3 | 가중치 경계값·단위 | `0.5`·`2.0`·기본 `1.0`·`0.1` 단위값과 범위 밖(`0.4`·`2.1`·비단위값) 설정 | 허용 범위·단위값 저장, 미설정 시 기본 `1.0`, 범위 밖은 거부 또는 조직 범위로 Clamp | §7.3 |

## 4. 충돌 처리 (TS-KNW-030~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-KNW-030 | P1·L4 | 상위-하위 tier 충돌 | Daon 지식과 사용자 파일이 상충하는 주제로 질의 | 상위 권위가 최종 기준, 하위 내용은 숨기지 않고 `충돌·대안` 표시 | §7.4, INV-5 |
| TS-KNW-031 | P2·L3 | 충돌 기록 완전성 | TS-KNW-030 실행 후 ConflictRecord 확인 | 충돌 Source, 문장, 버전, 적용·배제 사유가 모두 기록 | §7.4, §16 |
| TS-KNW-032 | P2·L4 | 동일 tier 내 충돌 | 사용자 파일 2개가 상충하는 내용으로 질의 | 조용히 병합하지 않고 충돌 표시, 결과 상태 `지식 충돌` | §7.4, §9.3 |
| TS-KNW-033 | P1·L4 | 중요 충돌 자동 판정·검토 차단 | 상위 권위(Daon 승인 지식/강제 RuleSet)와 상충해 결과에 영향을 주는 충돌 유도 | ConflictPolicyVersion으로 `critical`(또는 `material`) 자동 판정, `review_required=true`, Run/Output `needs_review` 전환, `IMPORTANT_KNOWLEDGE_CONFLICT` 사유, 승인·전달·생산 지식 등록 차단 | §7.4, §9.3, §18.2 |
| TS-KNW-034 | P3·L4 | 충돌 후 Source 버전 갱신 | 충돌 원인 Source를 새 버전으로 교체 후 재질의 | 새 Run은 새 버전 기준으로 충돌 재평가, 과거 Run의 충돌 기록은 불변 | §7.4, §8.1 |
| TS-KNW-035 | P2·L3 | 충돌 severity 3단계 구분 | 결과 무영향 차이 / 결과 영향 상충 / 강제 RuleSet·Daon 상충을 각각 유도 | 각각 `informational` / `material` / `critical`로 결정론적 판정. informational은 공개하되 차단하지 않고, material·critical은 검토 차단. 검토자는 상향 가능, 조직 잠금 판정은 하향 불가 | §7.4 |

## 5. 강제 RuleSet (TS-KNW-040~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-KNW-040 | P1·L4 | 강제 RuleSet 잠금 표시·해제 불가 | 강제 Binding이 있는 조직 Workspace에서 UI 해제 시도 + API 직접 해제 호출 | 두 경로 모두 거부, 잠금 상태 표시 | §7.2, INV-3 |
| TS-KNW-041 | P1·L3 | Snapshot 검증 항목 | 강제 RuleSetVersion Snapshot의 저장 내용 확인 | 서명·Digest·발행 시각·유효기간·폐기 상태가 확인된 불변 Snapshot | §8.4 |
| TS-KNW-042 | P1·L4 | Connector 중단 + 유효 Snapshot | Daon Connector 차단 후 허용 기간 내 Snapshot 보유 상태에서 적용 대상 Run 실행 | 검증된 Snapshot으로 계속 적용, Run 정상 진행 | §8.4, §21.2 |
| TS-KNW-043 | P1·L4 | 유효 Snapshot 없음 — fail-closed | Snapshot 만료·폐기 판정 불가 상태에서 적용 대상 Run 실행 | 해당 Run만 `policy_blocked` + `RULESET_UNAVAILABLE`. 강제 RuleSet 생략 실행 0건 | §8.4, INV-3 |
| TS-KNW-044 | P1·L4 | fail-closed의 범위 한정 | TS-KNW-043 상태에서 Source 등록·조회, 기승인 결과 열람, Binding 없는 다른 Workspace 실행 | 모두 정상 동작. 차단은 해당 Binding의 적용 대상 Run에만 한정 | §1.2, §21.2 |
| TS-KNW-045 | P1·L3 | Preflight와 본 평가 이중 적용 | 강제 RuleSet 위반 소지가 있는 요청 실행 | 파이프라인에서 Preflight(실행 전)와 본 평가(근거 구성 후)가 각각 수행되고 RuleEvaluation 기록 | §9.1 |
| TS-KNW-046 | P2·L4 | 사후 검증 위반 탐지 | 생성 결과가 RuleSet에 위반되는 상황 유도 | 근거·RuleSet 사후 검증에서 탐지, 결과 상태 `RuleSet 검토 필요` | §9.1, §9.3 |

## 6. 선택형 RuleSet (TS-KNW-050~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-KNW-050 | P2·L4 | 선택형 Binding 활성·비활성 | Workspace 관리자가 조직이 허용한 선택형 RuleSet을 켜고 끔 | 정상 반영, 정책 Version·ETag·Audit 기록 | §7.2, §17.2 |
| TS-KNW-051 | P1·L3 | 선택형 Binding 권한 경계 | 일반 사용자·편집자가 Binding 변경 시도, Workspace 관리자가 강제 Binding 변경 시도 | 모두 거부. 선택형=Workspace 관리자, 강제=조직 관리자만 | §7.2, §17.2 |
| TS-KNW-052 | P1·L4 | `warn_and_skip` 공개 생략 | 선택형 Snapshot 확보 불가 + `failure_mode=warn_and_skip`으로 Run 실행 | 계속 실행하되 화면과 RunSnapshot에 누락 공개. 묵시적 생략 0건 | §7.2 |
| TS-KNW-053 | P1·L4 | `block` 차단 | Snapshot 확보 불가 + `failure_mode=block` | `policy_blocked` 종료 | §7.2 |
| TS-KNW-054 | P2·L3 | 선택형도 Snapshot 고정 | 정상 상태에서 선택형 RuleSet 적용 Run 실행 | 실행 전 Version Snapshot 고정, Run 중 RuleSet 갱신이 진행 중 Run에 영향 없음 | §7.2, §16.1 |

## 7. LLM 일반지식 표시 (TS-KNW-060~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-KNW-060 | P1·L4 | 자체 지식 표시 | 문서 근거가 전혀 없는 일반 상식 질의 | 답변에 `LLM 자체 지식` 표시, 문서 인용 형식으로 위장 0건 | §8.5, INV-6 |
| TS-KNW-061 | P1·L4 | 혼합 답변의 구분 표시 | 일부는 문서 근거, 일부는 일반지식인 질의 | 문서 근거 부분은 인용, 일반지식 부분은 자체 지식 표시로 구분 | §8.5 |
| TS-KNW-062 | P2·L4 | 중요 주장 근거 부족 표시 | 중요한 판정성 질문을 근거 문서 없이 질의 | `근거 부족` 표시, 검증 수단(인터넷 검색·자료 추가) 안내 | §8.5, §9.3 |

## 8. 독립 실행 조건 (TS-KNW-070~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-KNW-070 | P1·L4 | Daon 미연결 독립 흐름 | Daon Connector 미설정 상태에서 §1.2의 8개 기능(Workspace 생성~생산 지식 등록) 순차 수행 | 전부 정상 동작 | §1.2, INV-1 |
| TS-KNW-071 | P1·L4 | Daon 장애 중 독립 기능 보존 | Daon 연결 후 장애 주입 → 독립 기능 수행 | Daon 지식·엔진만 `연결 불가`/`축소 운영` 표시, 나머지 정상 | §1.2, §21.2 |
| TS-KNW-072 | P2·L4 | Daon 재연결 복구 | 장애 해제 후 Daon 지식 질의 | 재연결 후 정상 검색, 장애 구간의 Run 기록 보존 | §19.2 |

## 설계 확인 필요 사항

- `[해소]` Q1(중요 충돌 판정) — §7.4 ConflictRecord.severity·ConflictPolicyVersion(R1-D013)으로 확정 (TS-KNW-033·035)
- `[해소]` Q2(가중치 척도·계층) — §7.3 `0.5~2.0`·단위 `0.1`·기본 `1.0`·최근접 단일 계층(R1-D014)으로 확정 (TS-KNW-021·024)

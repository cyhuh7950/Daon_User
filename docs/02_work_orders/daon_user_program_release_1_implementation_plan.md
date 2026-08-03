# Daon 사용자형 지식 업무지원 프로그램 Release 1 구현 작업계획서

## 문서 정보

| 항목 | 내용 |
| --- | --- |
| 문서 구분 | Release 1 구현 작업계획 정본 |
| 계획 ID | `DAON-USER-R1-PLAN` |
| 계획 버전 | `1.4` |
| 작성일 | 2026-07-20 |
| 최종 수정일 | 2026-08-04 |
| 상태 | 승인 · 신산님 · 2026-07-20 |
| 구현 상태 | `READY` |
| 대상 제품 | Daon 사용자형 지식 업무지원 프로그램 |
| 대상 Release | Release 1 — 핵심 업무형 |
| 상세 설계 정본 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` |
| 상세 설계 배포본 | `docs/Daon 사용자형 지식 업무지원 프로그램 상세 설계서.docx` |
| 상세 설계 정본 SHA-256 | `B35759D057822FCE688D22178B9C8C54331D84C653EB370FB45048A8464644BC` |
| 상세 설계 배포본 SHA-256 | `DAB1F8A936D69B18355EB986579A0CA5535169E829AB80D30DAC067606FEA0DF` |
| 계획 정본 경로 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` |
| 승인 기준 Manifest | `docs/02_work_orders/release_1_baseline_manifest.json` · M0 생성 예정 |
| 적용 Git 기준선 | 최초 문서 기준 Commit · 본 계획을 포함한 Initial Commit 생성 후 Baseline Manifest에 Commit Hash 고정 |
| 최종 승인자 | 신산님 |
| 설계·기술 책임자 | 어울1 |
| 개발 수행자 | 어울2 · 프로젝트 Custom Agent `daon-developer` |
| 외부 독립 검증 | CLAUDE · 현재의 독립 검증 방식 |

> 이 계획서는 작업 도중 수정할 수 있는 살아 있는 문서다. 다만 변경은 §3의 변경 통제 절차를 따르며, 승인된 기능 범위·요구사항·공개 API·데이터 계약·보안 경계·중요 위험을 문서 갱신 없이 바꾸지 않는다.

### 승인 기록

| Gate | 필요한 승인 | 현재 상태 | 승인자·일자 |
| --- | --- | --- | --- |
| G0-DESIGN | 상세 설계서 승인 | 승인 | 신산님 · 2026-07-20 · `APR-G0-DESIGN-20260720-01` |
| G0-PLAN | 본 Release 1 작업계획 승인 | 승인 | 신산님 · 2026-07-20 · `APR-G0-PLAN-20260720-01` |
| G0-BASELINE | M0 결정·추적표·환경 기준선 승인 | 승인 | 신산님 · 2026-07-20 · `APR-G0-BASELINE-20260720-01` |
| G2-UX | 전체 화면·상태·운영·복구 흐름 승인 | 미착수 | 미정 |
| G9-DEPLOY | 외부 환경 배포 대상·Rollback 사전 승인 | 조건부·미착수 | 미정 |
| G9-DRILL | 운영 데이터 Restore·파괴적 복구 훈련 사전 승인 | 조건부·미착수 | 미정 |
| G9-INDEPENDENT | CLAUDE 독립 검증 결과 수집 | 미착수 | 미정 |
| G9-RELEASE | Release 1 최종 완료 승인 | 미착수 | 미정 |

### 변경 이력

| 버전 | 일자 | 변경 내용 | 변경 등급 | 승인 상태 |
| --- | --- | --- | --- | --- |
| 0.1 | 2026-07-20 | 상세 설계서 기준 최초 Release 1 작업계획 작성 | 최초 작성 | 승인 대기 |
| 0.2 | 2026-07-20 | 독립 감사 반영: 승인 활동 분리, 명시 의존성, 75개 세분화 Work Order, C3·검증 Gate 보강 | C1 문서 보완 | 승인 대기 |
| 0.3 | 2026-07-20 | 신산님 결정과 설계 보강 반영: Vision/LLM-first, 생성 설정, Production-bound M2, 초기 Web Thin Vertical E2E, macOS Build Gate, 구 문서 폐기 | C2 승인 결정 + C0/C1 정합화 | 승인 대기 |
| 0.4 | 2026-07-20 | CP3 단일 PDF·단일 승인 모델 Core를 확장 Work Order와 분리, Source/ProcessingRun 상태와 Studio 승인 수명주기·iOS 증거 계약 보강 | C1 문서 보완 | 승인 대기 |
| 0.5 | 2026-07-20 | 신산님 승인 설계 질의 Q1·Q2·Q3·Q7·Q8·N1·N2·N3 반영: 중요 충돌·가중치·비용 차단·모바일 편집·Step-up·오디오 Ready Gate·waiting_model 재처리·현재 권한 재검증 계약 보강 | C2 승인 결정 + C1 정합화 | 설계 결정 승인·문서 전체 승인 대기 |
| 0.6 | 2026-07-20 | TP-0 승인 기록, 화면·same-origin API·3단계 배포 표준, 단계별 진행 복구 기록, 작업지시서/프롬프트 분리, 3회 미완료·실패 사용자 결정 Gate 반영 | C2 사용자 결정 + C1 운영 정합화 | 신산님 승인 |
| 0.7 | 2026-07-20 | G0-BASELINE 승인, R1-D001·D003·D009·D010 확정, 외부 차단 조건 유지, 구현 상태 `READY` 전환 | C0 승인 기록 | 신산님 승인 |
| 0.8 | 2026-07-20 | R1-M1-03 레지스트리 사전검증에 따라 Python 3.14.3·Tauri CLI 2.11.4·React Native 0.86.0을 정확 Pin하고 진행 복구 경로를 운영 규칙과 정합화 | C1 기술 정정 | 어울1 확정 |
| 1.0 | 2026-08-03 | 개발 기준을 `ssh WSL-server`로 확정하고 기존 `local-postgres:5432`·`postgres_env_default`·`proxy-network`를 사용하도록 정합화. ysna-server는 공용 `shared-db`·외부 `proxy-network`를 사용하며 기존 공용 자원은 변경하지 않음 | 신산님 지시 · 환경 정합화 |
| 1.0 | 2026-07-30 | R1-M5-05 Sync·Copy/Publish 공개 API 5종, Step-up 승인 Snapshot, 재개 전송, 명시적 충돌 선택과 자동 덮어쓰기 금지 계약 확정 | C2 사용자 승인 + C1 실행 정합화 | 신산님 승인 · `APR-R1-M5-05-SYNC-API-20260730-01` |
| 1.1 | 2026-07-31 | R1-M5-06 삭제·보존·Legal Hold 공개 API 6종, 상태·파생 Inventory·Local Copy Tombstone/Ack·최소 Audit 계보 계약 확정 | C2 사용자 승인 + C1 실행 정합화 | 신산님 승인 · `APR-R1-M5-06-RETENTION-API-20260731-01` |
| 1.2 | 2026-07-31 | R1-M5-07 Backup·격리 Restore Cloud API 7종, Local 손상 복구 API 3종, Preview·Execute 재검증·현재 Retention 우선·Fixture-only 계약 확정 | C2 사용자 승인 + C1 실행 정합화 | 신산님 승인 · `APR-R1-M5-07-RECOVERY-API-20260731-01` |
| 1.3 | 2026-08-04 | CP3 Go 결정 기록, M5~M8 증거·Milestone Exit 소급 검증, 내부 계약 완료와 실제 여정 검증 분리, 계획 버전·Work Order 추적성 정합화 | C2 사용자 승인 + C0 검증 부채 정리 | 신산님 승인 · `APR-CP3-GO-20260804-01` |
| 1.4 | 2026-08-05 | Provider 독립 구조·다중 LLM 후보·화면 기반 Provider/Model 선택·Secret 전용 `.env` 계약 반영 | C2 사용자 결정 + C1 정합화 | 신산님 승인 |

---

## 1. 목적

이 문서는 승인된 상세 설계서를 구현하기 위해 Release 1 작업을 M0부터 M9까지 나누고, 각 단계의 선행조건·Work Order·산출물·완료 증거·승인 Gate를 고정한다.

계획의 목적은 다음과 같다.

1. 전체 화면과 운영 흐름을 먼저 완성하고 승인받은 뒤 개별 기능을 구현한다.
2. 한 Work Order가 하나의 검증 가능한 계약 또는 사용자 흐름만 다루게 한다.
3. Web·Windows·iOS·Android와 Local-private·Cloud-sync를 실제 실행 증거로 검증한다.
4. 다섯 지식 유형, 권위·가중치, RuleSet, 선택형 LLM, Studio 계보를 하나의 수직 흐름으로 연결한다.
5. 어울1이 설계와 기술 판단을 소유하고 어울2가 승인 문서 전체를 기준으로 구현하게 한다.
6. 작업 도중 계획을 수정할 수 있게 하되 변경 근거와 승인 경계를 잃지 않는다.

이 문서는 구현 결과가 아니다. 이 문서가 승인되더라도 상세 설계서 승인과 M0 승인 기준선이 함께 충족되기 전까지 코드 수정은 시작하지 않는다.

## 2. 문서 우선순위와 해석 규칙

제품 기준 문서의 우선순위는 다음과 같다.

1. 신산님의 최신 명시 결정
2. 저장소 `AGENTS.md`의 상시 역할·권한·안전 규칙
3. 승인된 상세 설계 Markdown 정본
4. 승인된 본 Release 1 작업계획
5. Work Order와 작업보고서

운영 규칙:

- 프로젝트 `$daon-subagent-delivery` Skill은 제품 요구사항 우선순위를 정하는 문서가 아니라 `AGENTS.md`가 지정한 필수 인계 절차 정본이다. 상세 설계·계획·Work Order는 이 Skill의 결과 분류·재작업·인수 절차를 약화하거나 대체할 수 없다.
- 신산님의 최신 결정과 `AGENTS.md`가 충돌하는 것으로 보이면 임의 해석하지 않고 확인 후 `AGENTS.md`와 관련 문서를 먼저 동기화한다.
- 상세 설계와 본 계획이 충돌하면 상세 설계를 우선하고 현재 Work Order를 `BLOCKED`로 전환한다.
- Work Order가 본 계획을 바꾸지 못한다. 필요한 변경은 계획과 결정 기록을 먼저 갱신한다.
- DOCX 배포본과 Markdown 정본이 다르면 Markdown 정본을 우선한다.
- 기존 Daon2·Daon2.5·Daon3의 내부 DEV 번호, DB, 모듈과 파일 경로는 이 독립 제품의 구현 의존성으로 사용하지 않는다.
- `docs/daon_user_knowledge_work_support_independent_design.md`는 `SUPERSEDED` 안내 전용이며, 역사 보관본 `docs/99_archive/daon_user_knowledge_work_support_independent_design.md`도 구현 기준으로 사용하지 않는다. Vision/LLM-first 등 계승된 원칙은 현행 상세 설계 정본만 따른다.
- 일정 단축이나 구현 편의만을 이유로 실제 화면·보안·계보·운영 검증을 생략하지 않는다.

실행 보고와 작업 문서 규칙:

- 작업지시서는 설계·계획을 해당 Work Order 범위로 구체화한 구현 계약 정본이다. 작업지시 프롬프트는 작업지시서 경로·버전·Hash를 읽고 수행하라는 실행 명령만 담으며 구현 내용을 중복하지 않는다.
- 동일 작업지시서의 `FAILURE_REPORT` 또는 `INCOMPLETE` 원보고가 합계 3회에 도달하면 어울2의 쓰기를 중지하고 신산님에게 보고한다. 유효한 실패 횟수는 동일 `issue_id`의 정식 `FAILURE_REPORT`만 별도로 계산한다.
- 어울1의 직접 구현은 신산님 결정 후에만 `DIRECT_IMPLEMENTATION`을 선언하고 시작한다.
- TP-0·TP-1·TP-2A·TP-2·TP-3·TP-4·TP-5 도달 시 테스트 결과와 위험을 신산님에게 보고하고 다음 단계의 Go/No-Go 결정을 따른다.
- 중대 미진은 별도 수정 작업지시서로 재작업하고, 합격 가능한 경미 보완은 다음 작업지시서에 흡수한다. 사소한 사유로 합격 작업 전체를 다시 열지 않으며 검토 출력은 `판정 → 판단 이유 → 조치` 순서로 고정한다.

## 3. 계획 변경과 승인 통제

### 3.1 변경 등급

| 등급 | 변경 예 | 처리 권한 | 적용 조건 |
| --- | --- | --- | --- |
| C0 기록 변경 | 진행 상태, 증거 경로, 오탈자, 실제 완료 일자 | 어울1 | 동작·계약 변화 없음, 변경 이력 기록 |
| C1 내부 구현 변경 | 모듈 내부 구조, 라이브러리 교체, 같은 Milestone 안의 Work Order 순서 조정 | 어울1 기술 판단 | 요구사항·공개 계약·데이터·보안·중요 위험과 Gate·Milestone·선행 의존성 불변, 코드보다 문서 먼저 갱신 |
| C2 제품 계약 변경 | 기능 범위·우선순위·요구사항·공개 API·데이터 계약·보안 경계·중요 위험 | 신산님 사전 승인 | 영향 분석, 대안, 회귀·일정 영향과 변경 Diff 제출 |
| C3 운영 승인 변경 | 파괴적 작업, 외부 배포, 예외 수용, 최종 완료 판정 | 신산님 명시 승인 | 자동화 금지, 대상·복구·증거 명시 |

### 3.2 변경 절차

1. 발견 사항을 `change_id`로 등록한다.
2. 설계 조항, 영향 Work Order, 사용자 여정, API·데이터·보안·배포 영향을 분석한다.
3. C0/C1은 어울1이 판단하고 계획·결정 기록을 먼저 갱신한다.
4. C2/C3은 구현을 멈추고 신산님의 승인을 요청한다.
5. 변경 전후 Diff와 기존 완료 증거의 재검증 범위를 기록한다.
6. 계획 버전과 상세 설계 기준 Hash를 갱신한다.
7. 진행 중인 어울2에게 갱신된 전체 상세 설계서와 전체 계획서를 다시 전달한다.

Milestone 이동, G0/G2/G9 Gate 우회, M2 승인 전 개별 기능 구현 또는 데이터·보안 선행성을 뒤집는 재정렬은 C1로 처리할 수 없다. 외부 배포·운영 Restore·파괴적 장애 훈련은 실행 직전 승인 기록 ID, 정확한 대상, 영향, Rollback·복구 절차가 없으면 `BLOCKED`다.

### 3.3 일정과 노력 추정

본 계획은 달력 일정과 인원별 공수를 임의로 확정하지 않는다. 지원 OS·브라우저, 배포 계정, 서명 자격, Daon Sandbox, 모델 하드웨어와 실기기 확보가 M0에서 확정된 뒤 각 Work Order에 예상 시작·종료와 노력 범위를 추가한다. 일정 추가는 범위를 바꾸지 않는 한 C1 변경으로 관리한다.

## 4. 역할과 실행 원칙

### 4.1 역할

| 역할 | 책임 |
| --- | --- |
| 신산님 | 기능 범위·우선순위·중요 위험·최종 승인·진행 여부 결정 |
| 어울1 | 구현 종료까지 설계·기술 판단 소유, 계획·Work Order 갱신, 결과·근거 검토 |
| 어울2 | 승인된 상세 설계서와 계획 전체를 기준으로 한 Work Order 구현·테스트·정식 결과보고 |
| CLAUDE | 최신 설계 문서와 최종 변경사항의 외부 독립 검증 |

### 4.2 단일 Writer 원칙

- 코드 수정은 한 시점에 한 역할만 수행한다.
- 어울2가 한 Work Order를 구현하는 동안 어울1이나 다른 개발 Agent는 그 Work Order 범위를 수정하지 않는다.
- 어울1은 구현을 인수하기 전에 어울2의 쓰기를 중지하고 현재 Diff·테스트·남은 작업을 회수한다.
- 읽기 전용 분석·감사는 병행할 수 있으나 쓰기 작업과 혼동하지 않는다.

### 4.3 화면·운영 우선 원칙

- 사용자가 실제로 보는 전체 그림, 화면 상태, 오류·복구와 운영 화면을 M2에서 먼저 완성한다.
- G2-UX 승인 전에는 개별 업무 기능 구현을 시작하지 않는다.
- 사용자와 운영자가 Python·DB·CLI를 직접 실행해야 하는 기능은 완료로 인정하지 않는다.
- M2는 폐기형 Prototype이 아니라 M3가 승계하는 Production-bound UI 기준선이다. IA·Route·Design Token·상태 모델·접근성 Component·반응형 Layout을 재사용한다.
- Mock은 교체 가능한 Adapter 경계에만 명시적으로 사용할 수 있고 Production 성공 경로로 남길 수 없다.
- 정적 검사, Build 성공 또는 HTTP 200만으로 완료를 판정하지 않는다.

## 5. Release 1 범위

### 5.1 포함 범위

- Daon2·Daon2.5·Daon3과 독립된 Web·Windows·iOS·Android 제품
- 적응형 3면 Workspace: 자료·지식, 대화·실행, 업무 Studio
- 개인·조직 Workspace와 Local-private·Cloud-sync 데이터 영역
- 사용자 파일·직접 입력, 인터넷, LLM 일반지식, Daon 승인 지식·RuleSet, 생산 지식
- Daon 승인 지식 우선 권위, 사용자 가중치, Clamp, 충돌 표시
- PDF, DOCX, PPTX, XLSX, CSV, TXT, Markdown, 주요 이미지, M4A, WAV, MP3
- Vision/LLM-first 문서·표·이미지 의미 이해, Parser·OCR 검증·보완, Audio-capable LLM 또는 Local ASR+LLM 음성 의미 이해와 시간 구간 계보
- Local·Internal·External LLM, `auto`·`local_only`·`pinned` 선택과 안전한 Fallback
- Daon Standard API와 인터넷 검색·Safe Fetch Connector
- 출처 기반 질문·요약·비교·점검, 인용과 근거 Viewer
- 다섯 Studio 산출물, 편집·버전·검토·승인·내보내기·전달·생산 지식 등록
- 운영 상태·경고·재처리·업데이트·Rollback·Backup·Restore 화면
- 설치·업데이트 가능한 Windows App, Android APK, iOS Archive 또는 설치 Build

### 5.2 제외 범위

- 사용자 생산 지식의 Daon 승인 지식 자동 승격
- Daon 내부 DB·모듈·파일 경로 직접 연동
- 모바일 기기 자체 온디바이스 LLM
- 실시간 동시 문서 편집
- 무승인 외부 시스템 변경
- 완전 자율형 Agent 업무 실행
- 오디오·비디오 완성본 생성
- Release 2의 슬라이드·인포그래픽·카드·퀴즈·공동 댓글
- Release 3의 멀티미디어 브리핑·승인형 Tool/Agent 실행

### 5.3 플랫폼 책임

| 플랫폼 | Release 1 책임 |
| --- | --- |
| Web | 전체 Workspace·지식·대화·Studio·검토·승인·운영 기능 |
| Windows | Web 전체 기능, Local-private, Loopback Local API, Managed Local Model, Offline·Reconnect |
| Android | Capture·조회·질문·근거·간단 편집·검토·승인·알림·제한 Offline |
| iOS | Capture·조회·질문·근거·간단 편집·검토·승인·알림·제한 Offline |

## 6. 구현 불변 조건

다음 조건은 모든 Work Order의 기본 완료 조건이다.

1. Daon 연결은 선택 사항이며 Daon 없이 독립 핵심 흐름이 동작한다.
2. Daon 내부 DB·서비스 URL·패키지·Runtime Image·소스 Import·파일 경로 직접 의존은 0건이다.
3. 강제 RuleSet은 사용자가 해제할 수 없고 유효 Snapshot이 없으면 적용 대상 Run만 안전하게 차단한다.
4. Daon 승인 지식은 다른 지식보다 높은 권위를 가지며 사용자 가중치가 권위를 뒤집지 못한다.
5. 충돌과 근거 부족을 숨기지 않고 상태·Source·버전·사유를 표시한다.
6. LLM 일반지식은 문서 인용으로 위장하지 않는다.
7. 생산 지식은 명시적 등록과 불변 버전으로만 Source가 된다.
8. Local-private 자료는 승인 없이 Cloud 또는 External Provider로 이동하지 않는다.
9. 모델 직접 선택도 권한·데이터 영역·외부 전송 정책을 우회하지 못한다.
10. 승인되지 않은 Provider·모델로 자동 Fallback하지 않는다.
11. 모든 Run은 지식·권위·가중치·RuleSet·모델 후보·전송 범위를 불변 Snapshot으로 남긴다.
12. 클라이언트는 내부 URL·Raw Provider Code·Secret·DB 주소를 보관하거나 전송하지 않는다.
13. 사용자·운영 흐름은 화면과 공개 API로 수행하며 Python·DB·CLI를 요구하지 않는다.
14. 모든 문서·표·이미지의 문맥·의미 이해와 의미 청킹은 Vision LLM 또는 LLM을 우선한다.
15. Parser·OCR·Document Parse는 검증·보완·원문 위치 재현만 담당하며 Parser-only 결과를 문서 이해 완료나 `ready`로 판정하지 않는다.

모든 Work Order가 지켜야 하는 공통 구현 표준:

- 기준 Desktop 화면은 1920×1080이고 기본 본문·Form 12px, 작은 설명 10px, 아주 작은 보조 9px, Sidebar 제목 14px, 화면 제목 16px를 적용한다.
- 상시 설명 박스를 금지하고 `i` 아이콘·Tooltip·Popover를 사용하되 필수 오류·경고·진행 상태는 별도 상태 영역과 복구 동작으로 노출한다.
- Browser 코드는 same-origin 상대 경로만 사용하며 API 절대주소, `localhost`, `127.0.0.1`, Docker 내부 Host·Port와 `NEXT_PUBLIC_API_BASE_URL` Client Fetch를 금지한다.
- 개발은 로컬 수정·기본 검증 → Git Push → `ssh WSL-server`의 기존 Docker 환경에서 통합 테스트 → ysna-server Git 기준선 배포 → 서버 통합 테스트 → PR Merge 순서를 따른다. WSL-server의 `local-postgres:5432`·`postgres_env_default`·`proxy-network`를 사용하며, ysna-server는 `/home/ubuntu/deploy/daon-user`에서 공용 `shared-db`·`proxy-network`를 사용한다. 기존 공용 컨테이너·DB·Volume은 변경하지 않는다.
- Daon Web은 `3330`을 사용하고 `proxy-network`에 연결한다. Browser는 same-origin Proxy/BFF만 호출한다. ARM64 또는 Multi-arch Image만 허용한다.
- Migration은 사전점검·Backup·적용·Rollback 검증을 한 작업 단위로 기록하고, 배포 Commit SHA·Service Health·서버 테스트 결과가 모두 있어야 PR Merge로 진행한다.

## 7. 구현 시작 Gate와 M0 필수 결정

### 7.1 구현 시작 Gate

아래가 모두 충족되기 전 구현 상태는 `BLOCKED`다.

- G0-DESIGN: 상세 설계서 상태가 승인으로 변경되고 승인자·일자가 기록되며 Q1·Q2·Q3·Q7·Q8·N1·N2·N3 `DESIGN_OPEN=0`과 설계 반영 증거가 확인됨
- G0-PLAN: 본 계획 상태가 승인으로 변경되고 승인자·일자가 기록됨
- G0-BASELINE: 핵심 미확정 0건, 결정 기록·추적표·환경·증거 기준선이 신산님에게 승인되고 승인자·일자가 기록됨
- `release_1_baseline_manifest.json`에 승인된 설계·계획 Hash, 문서 기준 Commit, 결정·추적표 Hash, 승인 기록 ID 고정
- Git 문서 기준 Commit과 기존 사용자 파일 보존 상태 확인
- 동일 범위를 쓰는 다른 Agent 없음 확인

### 7.2 M0에서 확정할 항목

| 결정 ID | 확정 항목 | 결정 기준 | 승인·확보 주체 |
| --- | --- | --- | --- |
| R1-D001 | 지원 Windows·브라우저·Android·iOS 최소 버전 | 실제 사용자 대상과 보안 지원 기간 | 신산님 승인 |
| R1-D002 | Node·Python·Rust·Tauri·React Native·PostgreSQL, Xcode·CocoaPods 등 정확한 Pin | 상호 호환성, LTS, Windows·macOS CI·배포 재현성 | 어울1 판단·기록 |
| R1-D003 | Release 1 Pilot 배포 형태 | Managed Cloud, 전용 Cloud, On-prem, Hybrid 범위 | 신산님 승인 |
| R1-D004 | Identity Provider·조직 Provisioning 방식 | OIDC/PKCE, 조직 계정, 장치 인증 | 신산님 환경 확인 + 어울1 설계 |
| R1-D005 | Object Storage·Queue·Secret Store·Embedded Vector 구현체 | 설계 계약 준수, 운영·복구 가능성 | 어울1 판단·기록 |
| R1-D006 | Local/Internal/External 모델·ASR·Embedding·Reranker Allowlist | 품질, 라이선스, 하드웨어, 데이터 영역 | 중요 위험은 신산님 승인 |
| R1-D007 | Daon Sandbox Connector 계약·자격 증명·호환 버전 | 표준 API, 읽기·실행 범위, 오류·Timeout | 신산님 접근 승인 + 어울1 계약 |
| R1-D008 | 인터넷 검색 Provider·Safe Fetch 정책 | License, Allowlist, SSRF 방어, 비용 | 중요 위험은 신산님 승인 |
| R1-D009 | 데이터 분류·Region·보존·Legal Hold·RTO/RPO | 개인·조직 정책과 복구 목표 | 신산님 승인 |
| R1-D010 | 파일 크기·동시 실행·응답시간·비용 한도 | 실제 Pilot 규모와 운영 SLO | 신산님 승인 |
| R1-D011 | Windows 서명·Android Keystore·Apple Developer Team·Signing Identity·Provisioning Profile·알림 계정 | 설치·업데이트·배포 검증 가능성 | 신산님 계정·권한 제공 |
| R1-D012 | 실제 Browser·Windows PC·Android Device·iOS Device/Simulator, macOS Build Host 또는 macOS CI Runner 검증 환경 | R1 증거 Matrix와 실제 iOS Archive 충족 | 신산님 환경 확인 + 어울1 구성 |
| R1-D013 | 중요 충돌 판정 | ConflictPolicyVersion에 따른 결과 영향·Daon 승인 지식·강제 RuleSet 기준, 검토자 상향과 미해결 최종화 차단 | 확정 · 신산님 승인 2026-07-20 |
| R1-D014 | 사용자 가중치 척도·우선순위 | 기본 1.0, 0.5~2.0, 0.1 단위, 개별 Source→그룹→유형→기본값 중 하나만 적용 | 확정 · 신산님 승인 2026-07-20 |
| R1-D015 | 비용 한도 종료 계약 | `policy_blocked/COST_LIMIT_EXCEEDED`, 동일 Frozen Context 자동 재시도 금지, 승인 변경 후 새 Run | 확정 · 신산님 승인 2026-07-20 |
| R1-D016 | 모바일 간단 편집 화이트리스트 | 제목·기존 텍스트·단순 표 Cell·검토·승인 허용, 구조·Layout·근거 연결·일괄 재생성 제외 | 확정 · 신산님 승인 2026-07-20 |
| R1-D017 | 민감 작업 추가 인증 | 외부 전송·영역 이동·외부 전달·생산 지식 등록·조직 정책·장치 철회·영구 삭제·Restore의 Step-up | 확정 · 신산님 승인 2026-07-20 |
| R1-D018 | 오디오 처리 상태와 Ready Gate | Audio-capable LLM 또는 ASR+LLM 의미 이해, 시간 근거 검증, ASR-only `ready` 금지 | 확정 · 신산님 승인 2026-07-20 |
| R1-D019 | `waiting_model` 재처리 | Readiness Event 제한 자동 재큐 + 권한 사용자 수동 재처리, 현재 정책의 새 ProcessingRun과 중복 억제 | 확정 · 신산님 승인 2026-07-20 |
| R1-D020 | 권한 변경 후 과거 결과 | OutputVersion 불변 보존, 현재 ACL 재검증, 무권한 근거·파생부 마스킹/차단, 현재 정책 새 Run | 확정 · 신산님 승인 2026-07-20 |
| R1-D023 | iOS 알림 설정 진입 | 기존 범용 앱 설정 기능 보존 + 알림 전용 공개 API 진입, iOS 16+ `openNotificationSettingsURLString`, iOS 15.1 기존 앱 설정 Fallback, 비공개 URL·TCC 직접 조작 금지 | 확정 · 신산님 승인 2026-07-28 |
| R1-D026 | 삭제·보존·Legal Hold 공개 계약 | 6개 API, 30일 유예, Audit 1년, Hold 우선, 영구 Purge·Hold의 결합 Step-up/현재 권한·정책 재검증, 파생 Inventory와 Known Local Copy Tombstone/Ack | 확정 · 신산님 승인 2026-07-31 · `APR-R1-M5-06-RETENTION-API-20260731-01` |
| R1-D027 | Backup·Restore·Local 손상 복구 공개 계약 | Cloud 7개 API, Local Loopback 3개 API, RPO 15분, Preview 후 별도 Step-up Execute, 현재 Retention·Hold·Tombstone 우선, Fixture-only 격리 Restore와 G9-DRILL 경계 | 확정 · 신산님 승인 2026-07-31 · `APR-R1-M5-07-RECOVERY-API-20260731-01` |

R1-D013~020은 이번 승인으로 설계 계약이 확정되었으므로 M0에서 재결정하지 않고 결정 기록·추적표·Baseline Manifest에 고정한다. 나머지 미확정 항목은 추측으로 채우지 않는다. M1 진입 전에 각 항목을 `확정`, `범위 제외 승인`, `외부 차단` 중 하나로 분류하고 근거를 기록한다.

### 7.3 승인·검증 Gate 계약

| Gate | 소유자 | 필수 입력 | 승인·검증 산출물 |
| --- | --- | --- | --- |
| G0-DESIGN | 신산님 | 상세 설계 정본·배포본, 독립 감사 결과 | 승인자·일자·조건이 있는 설계 승인 기록 |
| G0-PLAN | 신산님 | 본 계획, 설계 추적 요약, 변경 통제 | 승인자·일자·조건이 있는 계획 승인 기록 |
| G0-BASELINE | 신산님 | R1-M0-A01~A04 산출물, 문서 기준 Commit | Baseline Manifest와 `BLOCKED→READY` 승인 기록 |
| G2-UX | 신산님 | R1-M2-08 기술 Evidence Pack, 어울1 영향 검토 | 승인·수정요청, 승인자·일자·화면 기준 Version |
| G9-DEPLOY | 신산님 | 외부 배포 대상·변경·영향·Backup·Rollback | 실행 범위와 유효 시간이 있는 사전 승인 ID |
| G9-DRILL | 신산님 | 운영 Restore/파괴 훈련 대상·Backup·복구 조건 | 실행 범위와 유효 시간이 있는 사전 승인 ID |
| G9-INDEPENDENT | 어울1·CLAUDE | 최신 설계·계획·최종 Diff·Evidence Pack | CLAUDE 독립 검증 원문과 어울1 판정 |
| G9-RELEASE | 신산님 | 전체 R1 E2E, 독립 검증, 남은 위험·예외 | Release 1 최종 완료 승인·조건·일자 |

## 8. 단계 의존성과 실행 전략

```mermaid
flowchart LR
    M0["M0 승인 기준선"] --> M1["M1 독립 저장소"]
    M1 --> M2["M2 전체 UX·운영 흐름"]
    M2 --> G2["G2-UX 신산님 승인"]
    G2 --> M3["M3 실행형 Client Shell"]
    M3 --> M4["M4 API·인증"]
    M4 --> M5["M5 Local·Cloud Data"]
    M5 --> M6["M6 지식·LLM·Connector"]
    M6 --> M7["M7 Source→질문→근거"]
    M7 --> M8["M8 Studio 업무 완료"]
    M8 --> M9["M9 운영·Release 검증"]
    M9 --> G9["G9-RELEASE 신산님 승인"]
```

기본 실행 순서는 각 표의 `depends_on`이 모두 `COMPLETED` 또는 승인된 Gate가 된 Work Order 중 ID 순서다. ID가 앞서도 `depends_on`이 끝나지 않으면 시작하지 않는다. 선행조건이 충족되고 같은 파일·계약·데이터를 수정하지 않는 읽기 전용 검토는 병행할 수 있다. 구현 순서 변경은 영향 분석과 계획 갱신 후 C1로 처리한다.

각 Milestone의 Work Order는 표에 적힌 `depends_on` 외에도 직전 Milestone의 Exit Gate를 암묵적으로 요구한다. M3은 기술 Exit 외에 신산님의 G2-UX 승인을 요구한다. 표의 `depends_on`은 같은 Milestone 안의 추가 기술 선행성을 명시하며 이를 생략하거나 ID 순서로 대체하지 않는다.

### 8.1 Release 1 내부 체크포인트

다음 체크포인트는 별도 Release나 범위 축소가 아니라 실행 리스크를 조기에 발견하는 Go/No-Go 기준이다. 실패하면 해당 원인을 해결하고 어울1이 증거를 수락할 때까지 다음 확장을 중지한다.

| Checkpoint | 선행 완료 | 판정 기준 |
| --- | --- | --- |
| CP1 승인 기준선 | M0·M1 | 승인 문서·R1-D001~020·추적표·환경·Git 문서 기준 Commit·Manifest, 독립 Build |
| CP2 Production-bound UX | M2·M3 | 승인된 UI·상태·오류·권한 자산을 승계한 실제 Web·Windows·Android·iOS Shell과 M3 Exit 증거 |
| CP3 초기 Web Thin Vertical E2E | R1-M6-10 | 실제 로그인→Workspace→단일 PDF→Vision/LLM 이해→Parser/OCR 검증→색인→질문→인용 원문 열기 |
| CP4 지식·모델·Client Beta | M7 | 전체 Source·모델·Connector와 Client 핵심 흐름 |
| CP5 Studio Beta | M8 | 생성 설정을 포함한 5종 산출물 전체 수명주기 |
| RC 운영 검증 | M9 | 배포·Update·Alarm·Recovery·전체 회귀 |

#### CP3 Go 결정(2026-08-04)

신산님은 `APR-CP3-GO-20260804-01`로 CP3 실제 실행을 승인했다. 이는 CP3 통과를 면제하거나 확장 Gate를 완화하는 결정이 아니다. Provider 독립 구조에서 화면으로 선택한 승인 Provider·Model을 사용해 실제 Process·저장소·모델·브라우저 증거를 확보할 때까지 추가 형식·Connector·플랫폼 범위의 신규 확장은 중지한다. 이미 내부 계약 범위로 진행된 M6~M8 Work Order는 `CONTRACT_COMPLETE / JOURNEY_UNVERIFIED`로 재분류하고, CP3·Milestone Exit·Evidence Manifest 소급 검증을 선행한다.

| 항목 | 결정 |
| --- | --- |
| CP3 판정 | `GO_TO_EXECUTION` — 실제 Thin Vertical E2E 실행 준비·수행 |
| Provider | 화면에서 선택한 승인 Provider (Upstage 포함) |
| CP3 모델 | 화면에서 선택한 역할별 ModelDeployment·Model Artifact·Deployment Digest |
| Parser/OCR 검증·보완 | 선택 Provider의 Parser/OCR Adapter 전용. 의미 이해를 대체하지 않음 |
| `.env` 저장 범위 | API Key·Secret 등 비공개 인증정보만 저장 |
| Gate 완화 | 없음 |
| 선행 복구 | M5~M8 Evidence Manifest 및 M5~M7 Milestone Exit 소급 검증 |

### 8.2 단계 상태

| 상태 | 의미 |
| --- | --- |
| `BLOCKED` | 승인·환경·권한·설계 판단이 없어 시작 불가 |
| `NOT_STARTED` | Gate는 충족되지 않았거나 아직 순서가 아님 |
| `READY` | 선행조건·Work Order 패킷·검증 환경 준비 완료 |
| `IN_PROGRESS` | 지정된 단일 Writer가 구현 중 |
| `VERIFYING` | 구현 종료 후 증거·회귀·독립 검토 중 |
| `COMPLETED` | 완료 조건과 필수 증거가 승인됨 |
| `REWORK` | 유효한 실패보고 후 같은 `issue_id`로 재작업 중 |
| `CONTRACT_COMPLETE / JOURNEY_UNVERIFIED` | 내부 계약·정적 검증은 충족했으나 실제 사용자 여정·운영 증거가 없어 제품 완료로 확정하지 않은 상태 |

## 9. Milestone 요약

| 단계 | 사용자·운영 결과 | 선행조건 | 하위 실행 단위 | Exit Gate | 초기 상태 |
| --- | --- | --- | --- | --- | --- |
| M0 | 승인 문서·결정·추적·환경 기준선 | 없음 | R1-M0-A01~A04 · 설계자/승인자 Activity | 핵심 미확정 0, G0 승인 | `COMPLETED` |
| M1 | 재현 가능한 독립 저장소·CI | M0 | R1-M1-01~05 | Build, 독립성 검사 0건 | `NOT_STARTED` |
| M2 | 전 화면·상태·운영·복구 클릭 흐름 | M1 | R1-M2-01~08 | G2-UX 승인 | `NOT_STARTED` |
| M3 | 실제 실행되는 Web·EXE·Mobile Shell | M2 승인 | R1-M3-01~06 | 실제 Browser·EXE·Device 클릭 | `NOT_STARTED` |
| M4 | 공개 API·BFF·인증·권한·오류 계약 | M3 | R1-M4-01~07 | 실제 Auth·HTTP·Idempotency | `NOT_STARTED` |
| M5 | Cloud·Local 저장·Sync·복구 | M4 | R1-M5-01~07 | Migration·암호화·Backup·Restore | `NOT_STARTED` |
| M6 | Source·Retrieval·LLM·RuleSet·Connector | M5 | R1-M6-01~16 | 실제 Route·Network·Lineage | `NOT_STARTED` |
| M7 | Source 등록부터 질문·근거까지 수직 흐름 | M3~M6 | R1-M7-01~06 | 실제 파일·Client E2E | `NOT_STARTED` |
| M8 | 다섯 업무 산출물의 전체 수명주기 | M7 | R1-M8-01~13 | 실제 파일 Open·Version·Audit | `NOT_STARTED` |
| M9 | 배포·알림·복구·전체 Release 검증 | M1~M8 | R1-M9-01~10, R1-M9-V01~V02 | CLAUDE 검증 + G9 승인 | `NOT_STARTED` |

---

## 10. M0 — 승인 기준선

### 목표

상세 설계와 본 계획을 승인 가능한 기준선으로 고정하고, 구현 중 판단을 되돌릴 결정·추적·증거 체계를 만든다.

M0는 개발 Subagent Work Order가 아니다. 어울1이 문서·기술 기준선을 준비하고 신산님이 승인하는 Baseline Activity이며 `$daon-subagent-delivery`로 자동 전달하지 않는다.

| Activity | 소유자 | depends_on | 단일 목표 | 주요 산출물 | 필수 완료 증거 |
| --- | --- | --- | --- | --- | --- |
| R1-M0-A01 | 어울1·신산님 | G0-DESIGN, G0-PLAN | 문서·승인 기준선 고정 | 승인 상태·버전·Hash·승인자·승인일, 문서 기준 Commit, Baseline Manifest | 상세 설계·계획 승인 기록과 Manifest Hash 일치 |
| R1-M0-A02 | 어울1·신산님 | R1-M0-A01 | 핵심 미확정과 위험 결정 | `docs/01_architecture/DECISIONS.md`, 초기 Risk Register | R1-D001~020 전부 확정·승인·외부 차단 분류, R1-D013~020 승인 결정 원문·설계 반영 Hash 고정 |
| R1-M0-A03 | 어울1 | R1-M0-A02 | 설계 추적 기준선 | `docs/02_work_orders/release_1_traceability.md` | 설계 조항↔WO↔R1 여정↔테스트↔증거↔Gate 누락 0건 |
| R1-M0-A04 | 어울1·신산님 | R1-M0-A03 | 환경·증거·작업보고 기준선 | 환경 목록, 자격·장치 준비표, 증거·보고·Attempt Ledger 템플릿 | Web·Windows·Android·iOS·Daon·LLM·Backup 검증 가능성 확인 |

### M0 Exit Gate

- G0-DESIGN·G0-PLAN 승인 완료
- R1-D001~020 핵심 미확정 0건과 R1-D013~020 결정·추적 ID·설계 반영 증거 고정
- 모든 R1 여정에 담당 Work Order와 증거 위치 지정
- `release_1_baseline_manifest.json`에 승인자·승인일·승인 기록 ID, 설계·계획·결정·추적표 Hash와 문서 기준 Commit 고정
- Git 문서 기준 Commit과 기존 Dirty/Untracked 상태 보존 계획 확정
- 파괴적 작업·외부 배포·예외 수용 자동화 없음 확인
- 신산님의 G0-BASELINE 승인 완료 후에만 구현 상태를 `BLOCKED`에서 `READY`로 전환

## 11. M1 — 독립 저장소와 재현 가능한 기반

### 목표

Daon 계열 내부 구현과 분리된 정상 Git·Monorepo·Client/API 경계·CI·버전 기준선을 만든다.

| Work Order | depends_on | 단일 목표 | 주요 작업·산출물 | 필수 완료 증거 |
| --- | --- | --- | --- | --- |
| R1-M1-01 | G0-BASELINE | Git 개발 기준선 수립 | 개발 Branch·Commit·보호 정책, 문서 기준 Commit 승계, 기존 파일 보존 | 정상 `git status`, 기준 Commit 연결, 무관 파일 변경 0건 |
| R1-M1-02 | R1-M1-01 | Monorepo와 소유 경계 | Web, Tauri, Mobile, API, Local Service, 공용 Contract/Token Package 구조 | 각 App 독립 Build 경계, 순환 의존 0건 |
| R1-M1-03 | R1-M1-02 | Toolchain·Dependency Pin | Node·Python·Rust·RN·DB 버전 파일과 Lockfile | 개발·CI 동일 버전, 새 환경 재현 Build |
| R1-M1-04 | R1-M1-02, R1-M1-03 | 독립성 검사 계약 | Dependency Graph, 금지 URL·Import·Package·Image·Path 검사 | Daon 직접 의존·Connector 우회 0건 |
| R1-M1-05 | R1-M1-03, R1-M1-04 | CI와 품질·개발 통합 Gate | Lint·Type·Unit·Contract·Build·보안·독립성 Job, Git SHA 기반 ysna-server 격리 검증 계약 | Job 또는 필수 서버 검증 실패 시 Merge 차단, 결과 Artifact·로그·배포 SHA |

### M1 Exit Gate

- Web·Windows·Mobile·API·Local Service 기본 Build 성공
- Daon 내부 의존 0건과 표준 Connector 경계 증명
- CI와 로컬 개발 기준이 같은 버전·Lockfile을 사용
- 사용자가 Python·DB CLI를 실행할 운영 절차 없음

## 12. M2 — 전체 UX·운영 흐름

### 목표

백엔드 기능 구현 전에 전체 제품의 실제 화면, 정상·대기·오류·권한·축소 운영·복구 흐름을 클릭 가능한 형태로 완성하고 신산님의 승인을 받는다.

| Work Order | depends_on | 단일 목표 | 포함 화면·상태 | 어울2 제출 증거 |
| --- | --- | --- | --- | --- |
| R1-M2-01 | R1-M1-05 | 제품 IA·Design Token 확정 | 홈, Workspace, 전달함, 이력, 알림, 설정, 운영 상태 | 전역 Sitemap, Token, 접근성 기준, 화면 목록 |
| R1-M2-02 | R1-M2-01 | 적응형 3면 Workspace | 자료·지식, 대화·실행, Studio, 근거 Drawer | 1440+, 1024~1439, 600~1023, 599- 화면 전환·상태 보존 |
| R1-M2-03 | R1-M2-02 | Source·지식·권위 흐름 | 등록, Modality별 처리, Version, 권위, 가중치, 중요 충돌, RuleSet 잠금 | 0.5~2.0 가중치·적용 계층, 중요 충돌 검토 차단, 오디오 두 경로, 정상·waiting_model·partial_understanding·needs_review·정책 차단·실패·만료 상태 클릭 |
| R1-M2-04 | R1-M2-02, R1-M2-03 | Run·모델·근거 흐름 | auto/local_only/pinned, 진행, Fallback, Citation, 충돌, 비용 한도 | 선택 이유·정책 차단·근거 부족·waiting_user·COST_LIMIT_EXCEEDED 화면 |
| R1-M2-05 | R1-M2-02, R1-M2-04 | Studio 업무 흐름 | 다섯 산출물, 목적·독자·Source·RuleSet·분량·출력 형식·검토 조건 생성 설정, 편집, Version, 검토, 승인, Export, 지식 등록, 모바일 편집 화이트리스트 | 설정 잠금·확정부터 전체 수명주기와 재승인 조건, 모바일 허용·차단 작업 클릭 |
| R1-M2-06 | R1-M2-01, R1-M2-02 | 계정·조직·정책·장치 | 역할, 권한, Provider, RuleSet, 가중치 잠금, Device, Step-up, 과거 결과 현재 권한 | 잠금 이유·403·STEP_UP_REQUIRED·AccessDecision 마스킹/차단·Revoke 흐름 |
| R1-M2-07 | R1-M2-06 | 운영·알림·복구 | Service·Queue·Model·Node·Connector·비용·Backup·Update, waiting_model 자동·수동 재처리 | 경고→제한 자동/수동 새 Run→복구, 중복 억제와 Daon/LLM/Index/Evidence 장애 흐름 |
| R1-M2-08 | R1-M2-03, R1-M2-04, R1-M2-05, R1-M2-06, R1-M2-07 | 플랫폼별 Production-bound Prototype 증거 Pack | Web·Windows·Android·iOS 필수 여정 연결, 재사용 자산·Mock Adapter·M3 전환표 | 실제 Browser Prototype 클릭, 반응형·키보드·오류 상태, 재사용 계약과 Evidence Manifest |

### M2 Exit Gate

- 모든 화면과 상태가 클릭 가능한 한 흐름으로 연결됨
- 아직 없는 기능은 성공으로 위장하지 않고 `unavailable` 또는 명시 Mock 상태로 표시
- R1-WEB/WIN/AND/IOS/OPS 여정이 화면에서 끝까지 추적됨
- 접근성·반응형·상태 보존 검증 완료
- IA·Route·Token·상태·접근성 Component·Layout의 M3 재사용 목록과 Mock Adapter 교체 계획이 고정됨
- R1-M2-08의 어울2 `COMPLETED` 보고를 어울1이 대조한 뒤 Milestone 상태를 `VERIFYING`으로 전환
- 어울1이 기술 증거 Pack을 신산님에게 제시하고, 신산님의 별도 G2-UX 승인자·승인일·의견을 기록
- 신산님의 G2-UX 승인 전 M3 이후 개별 기능 구현 금지

## 13. M3 — 실행형 Client Shell

### 목표

승인된 M2의 Production-bound UI 자산을 승계해 실제 Production Web Process, Windows 설치형 App, Android APK, iOS Build에서 실행한다. 재사용하지 않는 부분은 사유·대체 구현·G2 승인 화면과의 차이 및 회귀 증거를 제출한다.

| Work Order | depends_on | 단일 목표 | 주요 산출물 | 필수 완료 증거 |
| --- | --- | --- | --- | --- |
| R1-M3-01 | G2-UX | Web 실행 Shell | Next.js Production App, same-origin BFF 경계, 반응형 UI | 실제 Chrome 클릭, Network·Console, 종료·재기동 |
| R1-M3-02 | G2-UX | Windows Tauri Shell | Tauri 2 App, 공용 React UI, 설치·실행·종료 구조 | 설치 EXE, 실제 App 클릭, Process·Window lifecycle |
| R1-M3-03 | R1-M3-02 | Windows Local Service Shell | Packaged Local Service, Loopback API·IPC, App Instance 인증 골격 | 외부 Listen 0건, Process 소유권·Allowlist 증거 |
| R1-M3-04 | G2-UX | React Native 공용 Shell | Navigation, Design Token, Domain/OpenAPI Client 경계 | 공용 Type·Token과 DOM UI 강제 공유 0건 |
| R1-M3-05 | R1-M3-04 | Android 설치 Shell | APK, Android 권한·Deep Link·Lifecycle | 실제 Android Device 클릭, Background·재기동 |
| R1-M3-06 | R1-M3-04 | iOS 설치 Shell | 승인된 macOS Build Host/CI Runner, 고정 Xcode·CocoaPods·RN Toolchain, Apple Team·Signing·Provisioning, iOS Archive/설치 Build, 권한·Deep Link·Lifecycle | Host·Toolchain·Team·서명·Provisioning 식별 정보, 실제 Archive/설치 Build와 Device/Simulator 클릭, Background·재기동 |

### M3 Exit Gate

- Web, Windows, Android, iOS에서 승인된 핵심 Navigation 실제 클릭
- Client Source에 내부 API·Provider URL·Secret 없음
- Windows Loopback 외부 Interface Listen 0건
- 종료 후 잔존 Process·Port 0건

## 14. M4 — 공개 API·인증·권한

### 목표

Web BFF와 Native Public API가 동일한 의미를 가지도록 OpenAPI·Auth·Tenant·권한·오류·비동기 상태 계약을 구현한다.

| Work Order | depends_on | 단일 목표 | 주요 계약 | 필수 완료 증거 |
| --- | --- | --- | --- | --- |
| R1-M4-01 | R1-M3-01, R1-M3-04 | OpenAPI v1 공통 계약 | Resource ID, Pagination, Filter, Error, ETag, Idempotency, SSE, COST_LIMIT_EXCEEDED·STEP_UP_REQUIRED·CURRENT_ACCESS_DENIED | Schema Diff, Contract Test, 안전 오류 응답 |
| R1-M4-02 | R1-M4-01 | Audit Event Core | Actor·Trace·Policy Version·변경 전후·Append-only 저장 계약 | Audit 단위·통합 테스트, 변조·누락 검사 |
| R1-M4-03 | R1-M4-01, R1-M4-02 | 사용자·조직·Session 인증 | OIDC Authorization Code+PKCE, Web Session, Native Token, Device, StepUpAuthorization, 최소 IAM 영속화 | 실제 로그인·갱신·철회·만료·401, 민감 작업 추가 인증·만료·재사용 거부와 Audit 흐름 |
| R1-M4-04 | R1-M4-02, R1-M4-03 | Tenant·Workspace 권한 | Membership, 역할, 세부 권한, 정책 상속, 과거 Output 현재 ACL·AccessDecision | 403/404 비노출, 권한 축소 후 원문·파생부 마스킹/차단과 현재 정책 새 Run, Tenant 교차 접근 0건, Audit 일치 |
| R1-M4-05 | R1-M4-01, R1-M4-03, R1-M4-04 | BFF·Gateway·FastAPI 경계 | same-origin Web, HTTPS Native, Trace ID, Graceful Shutdown | 실제 HTTP, Network URL, Process 종료·재기동 |
| R1-M4-06 | R1-M3-03, R1-M4-03, R1-M4-04 | Loopback Local API 보안 | 단기 Token, Process·Instance 검증, Capability/Command Allowlist | 외부 Interface 거부, 위조 Token·명령 거부 |
| R1-M4-07 | R1-M4-02, R1-M4-03, R1-M4-04 | Notification 기반 | Inbox, 읽음·전달 상태, 권한 있는 대상, 이벤트 연결 | 권한·정책·실행 상태 알림과 Audit 연결 |

### M4 Exit Gate

- 모든 Write에 권한·소유권·Idempotency·Optimistic Concurrency 적용
- Web BFF와 Native Gateway 응답 의미 일치
- Stack Trace·DB Host·Provider 원문·Secret 이름 노출 0건
- 민감 작업 Step-up 우회 0건, 권한 철회 후 과거 결과·원문 비인가 노출 0건
- Auth·Error·Idempotency·Graceful Shutdown 실제 검증

## 15. M5 — Local·Cloud Data와 Sync

### 목표

Cloud-sync와 Windows Local-private가 서로 다른 저장·실행 영역을 유지하면서 승인된 Copy/Publish와 복구를 지원하게 한다.

| Work Order | depends_on | 단일 목표 | 주요 산출물 | 필수 완료 증거 |
| --- | --- | --- | --- | --- |
| R1-M5-01 | R1-M4-04, R1-M4-05 | Cloud 정본·격리 | PostgreSQL+pgvector Migration, RLS, Service Authorization | Migration 재적용, Transaction, Tenant 격리 |
| R1-M5-02 | R1-M4-05, R1-M5-01 | Object·Queue·Worker 저장 | S3 호환 Object, Prefix Policy, 비동기 Queue·Worker | 원본·산출물 Digest, 실패 Queue·재처리 |
| R1-M5-03 | R1-M4-06 | Local 암호화 저장 | SQLite, File Store, Embedded Vector Adapter, OS Secure Store Key | Restart, 암호화, Vector 검색, Key 철회 |
| R1-M5-04 | R1-M5-01, R1-M5-02, R1-M5-03 | 데이터 정본·계보 | Source/Run/RuleSet/Model/Studio/Audit Entity와 불변 Version | Migration·FK·상태 전이·Snapshot 불변 테스트 |
| R1-M5-05 | R1-M4-02, R1-M5-03, R1-M5-04 | Sync·Copy/Publish·충돌 | Preview·Step-up 승인 Snapshot, 승인 항목 재개 전송, Version 비교, 암호화 Offline Queue, Reconnect, 명시적 충돌 선택 API | 무승인 전송 0건, 원본 암묵 변경 0건, 자동 병합·덮어쓰기 0건, Batch 재개·Audit 연결 |
| R1-M5-06 | R1-M5-04, R1-M5-05 | 삭제·보존·Legal Hold | 공개 API 6종, `requested→deactivated→grace_period→cleanup_pending→purged`와 대체 상태, Migration `0005`, 정규화 파생 Inventory·Append-only Attempt/Audit/Trace, Local Tombstone/Ack, 현재 권한·정책·결합 Step-up·Idempotency·If-Match | TDD 부정 경로, PostgreSQL 18.4 `0001→0005`·재적용·`0005→0004→0005`, RLS·Runtime/OpenAPI 6 Route, 부분 실패 재시도·중복 삭제 0, Local 암호화 Restart/Ack, 최소 Audit 계보, 전용 Fixture만 Purge하고 기존 데이터 불변 |
| R1-M5-07 | R1-M5-01, R1-M5-03, R1-M5-04, R1-M5-06 | Backup·Restore·손상 복구 | Cloud 공개 API 7종, Local Loopback API 3종, Migration `0006`, 자동·수동 Backup, Preview→별도 Step-up Execute→Fixture-only 격리 Restore, 암호화 Local 격리·제한 복구, M2 `BackupRestoreAdapter` 승계 | TDD 부정 경로, PostgreSQL 18.4 `0001→0006`·재적용·`0006→0005→0006`, RLS·MinIO Checksum/누락, Runtime/OpenAPI 7+3 Route, 현재 Retention·Hold·Tombstone 우선과 Purge 부활 0, Local Restart·Repair·수동복구, actual 화면/API·same-origin Network, G9-DRILL 없는 운영 대상 Fail-close |

### M5 Exit Gate

- Local-private와 Cloud-sync 경로를 별도 테스트로 통과
- 저장·전송 암호화, Tenant·Workspace·영역별 Key 분리 증거
- 승인 없는 영역 이동과 External 전송 0건
- Backup·Restore와 Local 손상 복구를 Web·Windows 화면/API에서 확인하고 Browser Network의 Cloud 호출이 same-origin임을 증명
- Restore Preview·Execute별 현재 권한·정책·Step-up 재검증, Fixture Allowlist 밖 Restore 0건, Purge된 콘텐츠 부활 0건
- 운영 대상 Restore·파괴적 손상 주입은 G9-DRILL 승인 전 Fail-close

## 16. M6 — Source·지식·LLM·Connector

### 목표

지원 자료를 안전하게 Source로 처리하고, 권위·가중치·RuleSet·선택형 LLM·Connector를 실제 Route와 계보로 연결한다.

| Work Order | depends_on | 단일 목표 | 주요 범위 | 필수 완료 증거 |
| --- | --- | --- | --- | --- |
| R1-M6-01 | R1-M5-04 | CP3 Core Model Registry·Adapter | 단일 승인 Vision/문서입력 LLM Deployment, Artifact·Deployment·Binding 최소 계약 | 실제 모델 입력·출력·Digest·Health와 고정 Deployment 증거 |
| R1-M6-02 | R1-M4-02, R1-M5-04, R1-M6-01 | CP3 Core Routing·Egress | 단일 승인 모델의 고정 Route, Hard Filter, Frozen Policy, ModelAttempt·RunResult·EgressDecision; 자동 Fallback 없음 | UI 선택=Route·Model·Network·Egress·Audit 일치 |
| R1-M6-03 | R1-M3-03, R1-M5-03, R1-M6-10, R1-M6-14 | Managed Local Model | 하드웨어 진단, 추천, 다운로드·서명·Digest, 설치·시험·Update·Rollback·삭제 | 사용자 CLI 0건, 실제 Local Deployment lifecycle |
| R1-M6-04 | R1-M4-03, R1-M4-04, R1-M5-03, R1-M5-05, R1-M6-03 | Device·Local Node·Relay | Pairing, Device Identity, 단기 인증서·회전, Outbound-only, Relay 인가, Revoke | 공개 Inbound 0건, Web·Mobile 보안 접근, 장치 폐기 후 Key 무효화 |
| R1-M6-05 | R1-M5-02, R1-M5-04 | Source 등록·보안 검사 | 파일 MIME/실형식·악성·압축폭탄·암호화·손상, 직접 입력 SourceVersion·편집·재색인·민감정보/Injection 검사 | 허용/거부 형식 Matrix, 직접 입력 Version, 원본 Digest 보존 |
| R1-M6-06 | R1-M6-01, R1-M6-02, R1-M6-05 | CP3 단일 PDF Vision/LLM-first 이해 | 단일 PDF 원본의 Vision/LLM 의미·문맥 이해와 의미 청킹 후 Parser·OCR 검증·보완, Page·Chunk·EvidenceSpan | Parser-only `ready` 0건, 모델·Prompt·Policy·보조 도구 계보, 불일치 검토, PDF Page 재현 |
| R1-M6-07 | R1-M6-05, R1-M6-10, R1-M6-14 | 오디오 의미 이해·ASR 계보 | M4A/WAV/MP3, audio_understanding 또는 speech_to_text→LLM 의미 이해, Transcript·Timecode 검증·Evidence reconciliation | 두 승인 경로, Local ASR, 시간 구간, Model Digest·Policy 계보, ASR-only `ready` 0건 |
| R1-M6-08 | R1-M6-01, R1-M6-02, R1-M6-06 | CP3 Core Index·Retrieval | 단일 PDF의 실제 Index·검색·질문 입력 구성 | 질문에 필요한 Chunk 검색과 SourceVersion 고정 |
| R1-M6-09 | R1-M6-08 | CP3 Core 근거·결과 상태 | PDF Page Citation, 충분·부분·부족 상태 | Citation 클릭 시 당시 SourceVersion Page·문맥 재현 |
| R1-M6-10 | R1-M3-01, R1-M4-05, R1-M5-02, R1-M5-04, R1-M6-02, R1-M6-06, R1-M6-08, R1-M6-09 | 핵심 비동기 Run·초기 Web Thin Vertical E2E | accepted→planning→retrieving→generating→validating→completed, 로그인→Workspace→단일 PDF→Vision/LLM 이해→Parser/OCR 검증→색인→질문→인용 원문 열기 | 실제 Process·DB·Object·모델·Production Chrome, Mock 0건, RunSnapshot·ModelAttempt·Citation·Audit 일치 |
| R1-M6-11 | R1-M6-05, R1-M6-10, R1-M6-16 | 인터넷 Connector | Search Adapter, SSRF/Redirect 방어, Safe Fetch Snapshot | URL·게시/조회 시각·License·변경 Version |
| R1-M6-12 | R1-M4-04, R1-M6-05, R1-M6-10, R1-M6-16 | Daon 승인 지식 Connector | 승인 지식 Read/Search, Version·권한·유효기간·장애 | API Version·Auth·Timeout·Retry·Disconnect·Reconnect |
| R1-M6-13 | R1-M4-02, R1-M6-10, R1-M6-12, R1-M6-16 | RuleSet Connector·Binding | 선택/강제 Binding, Version Snapshot, 평가, 만료·폐기·failure_mode | warn_and_skip·block·RULESET_UNAVAILABLE과 Audit |
| R1-M6-14 | R1-M6-01, R1-M6-02, R1-M6-10 | 모델·Routing 확장 | Local/Internal/External, text·vision·audio_understanding·speech_to_text·embedding·reranker, auto·local_only·pinned, Readiness·Fallback·Capacity·비용 한도·waiting_model 재처리 | 역할별 실제 호출·Digest·Health·Capacity, COST_LIMIT_EXCEEDED, 제한 자동·수동 새 ProcessingRun, 불변 실패 Run·중복 억제, Frozen 후보·Fallback·Egress 결정표 |
| R1-M6-15 | R1-M6-06, R1-M6-10, R1-M6-14 | 문서·표·이미지 형식 확장 | DOCX·PPTX·XLSX·CSV·TXT·Markdown·주요 이미지의 Vision/LLM-first 이해 후 Parser/OCR 검증·보완, Cell·Region Evidence | 각 지원 형식의 의미 이해 계보, Parser-only `ready` 0건, 원문 위치 재현 |
| R1-M6-16 | R1-M6-08, R1-M6-09, R1-M6-10, R1-M6-14, R1-M6-15 | 전체 권위·가중 Retrieval·충돌 | Hybrid Search, Tier별 슬롯, Weight 0.5~2.0·최근접 계층·Clamp, Embedding·Reranker, ConflictPolicyVersion·ConflictRecord, 6종 결과 상태 | 상위 권위 보존, 중복 곱 0건, 경계값, 중요 충돌 자동 판정·검토 차단, 같은 Tier 정렬, Recall, 적용/배제 사유 |

### M6 Exit Gate

- R1-M6-10 CP3 Web Thin Vertical E2E가 실제 Process·저장소·단일 승인 모델로 통과한 뒤 R1-M6-03·04·07·11~16의 추가 형식·Provider·Connector·플랫폼 범위를 확장
- 지원 Source 형식과 오디오 직접 이해·ASR+LLM 처리·재처리·시간 Evidence 일치, ASR-only `ready` 0건
- 정책 Hard Filter 후보 0은 ProcessingRun `policy_blocked`·Source `needs_review`, 정책 허용 후보의 Runtime 실패 소진은 ProcessingRun `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`·Source `waiting_model`, Parser-only `ready` 0건
- `partial_understanding` Source는 기본 검색·생성 제외, 누락 범위 표시와 재처리·검토·비활성화 전이 검증
- `waiting_model`은 Readiness Event 제한 자동 재큐와 권한 사용자 수동 재처리로 새 ProcessingRun을 만들고 성공 시 `ready`, 중복 활성 Run·재처리 폭주 0건
- 다섯 지식 유형, 권위, 가중치 범위·계층·Clamp, 중요 충돌 자동 판정과 미해결 최종화 차단 검증
- `auto`, `local_only(device_only/private_org_allowed)`, `pinned`와 Local·Internal·External 실제 호출 검증
- 정책 후보 0은 `policy_blocked`, Runtime 후보 0은 재시도 가능한 `failed/NO_AVAILABLE_DEPLOYMENT`, 허용된 pinned 후보의 Runtime 문제는 `waiting_user`, 인증·잘못된 요청은 우회 없는 재시도 불가 `failed`로 검증
- 비용 한도 도달은 `policy_blocked/COST_LIMIT_EXCEEDED`, 동일 Frozen Context 자동 재시도 0건, 승인된 한도·정책 변경 뒤 현재 권한 새 Run 검증
- Local-private→External 자동 Fallback, pinned 모델 무단 변경, Stream 일부 출력 후 다른 모델 이어쓰기 0건
- 강제 RuleSet 유효 Snapshot 없음 시 적용 Run만 `policy_blocked/RULESET_UNAVAILABLE`
- 선택형 RuleSet `warn_and_skip`과 `block`이 UI·RunSnapshot에 공개됨
- Daon 연결·미연결·장애·재연결에서 독립 기능 보존

## 17. M7 — Source에서 질문·근거까지 수직 흐름

### 목표

실제 파일과 실제 Client를 사용해 Source 등록→처리→Index→질문→근거·충돌·계보 흐름을 완성한다.

| Work Order | depends_on | 단일 사용자 흐름 | 연결 여정 | 필수 완료 증거 |
| --- | --- | --- | --- | --- |
| R1-M7-01 | R1-M3-01, R1-M4-05, R1-M5-04, R1-M6-10, R1-M6-11, R1-M6-12, R1-M6-13, R1-M6-14, R1-M6-15, R1-M6-16 | Web Cloud-sync 지식 대화 | R1-WEB-01 | 파일·직접 입력·인터넷·Daon 범위, Production Chrome, Network·Console, RunSnapshot |
| R1-M7-02 | R1-M3-02, R1-M3-03, R1-M5-03, R1-M6-03, R1-M6-07, R1-M6-10, R1-M6-14, R1-M6-15, R1-M6-16 | Windows Local-private Offline 지식 대화 | R1-WIN-01 일부 | EXE, Local ASR, Network 차단, 로컬 검색·질문·근거, 외부 연결 0건 |
| R1-M7-03 | R1-M3-02, R1-M4-05, R1-M5-05, R1-M6-10, R1-M6-12, R1-M6-13, R1-M6-14, R1-M6-16 | Windows Cloud 모델·Daon 선택 | R1-WIN-02 | Local/Internal/External/Daon Route·Fallback·Egress·Audit |
| R1-M7-04 | R1-M3-05, R1-M4-05, R1-M5-05, R1-M6-04, R1-M6-07, R1-M6-10, R1-M6-14, R1-M6-15, R1-M6-16 | Android Capture→질문→근거 | R1-AND-01 일부 | APK·실기기, 파일·카메라·마이크, ASR·Background·Offline·Reconnect |
| R1-M7-05 | R1-M3-06, R1-M4-05, R1-M5-05, R1-M6-04, R1-M6-07, R1-M6-10, R1-M6-14, R1-M6-15, R1-M6-16 | iOS Capture→질문→근거 | R1-IOS-01 일부 | Archive/설치 Build, Device/Simulator, 권한·ASR·Background·Offline·Reconnect |
| R1-M7-06 | R1-M7-01, R1-M7-02, R1-M7-03, R1-M7-04, R1-M7-05 | 오류·만료·축소 운영 회귀 | 전체 | Source 만료, Index/Daon/LLM 장애, Evidence Store 차단, Reconnect |

### M7 Exit Gate

- 실제 파일·이미지·음성으로 각 Client 수직 흐름 통과
- 원문 Page·Cell·Image Region·Time Segment 인용 재현
- Local-private 외부 자동 전송 0건
- Daon 장애가 Binding 없는 독립 Workspace를 차단하지 않음
- 긴 Run의 진행·취소·재시도·waiting_user 상태가 화면과 Audit에 일치

## 18. M8 — 업무 Studio

### 목표

질문 결과를 실제 업무 산출물로 만들고 편집·버전·검토·승인·전달·생산 지식 등록까지 완료한다.

| Work Order | depends_on | 단일 목표 | 산출물·여정 | 필수 완료 증거 |
| --- | --- | --- | --- | --- |
| R1-M8-01 | R1-M7-06 | Studio 생성 설정·공통 계약 | GenerationRequest, GenerationSettingsSnapshot, StudioOutput, OutputVersion, EvidenceReference, 상태 전이 | 목적·독자·Source·RuleSet·분량·출력 형식·검토 조건 잠금, 제출 전 변경은 재확정·Revision 0건, 제출 후 변경은 새 Request·Revision, 불변 Version·previous_version_id·변경 사유 |
| R1-M8-02 | R1-M8-01 | 근거 기반 보고서 | DOCX·PDF | 요약·본문·결론·인용·경고·미확인, 실제 Open·Layout |
| R1-M8-03 | R1-M8-01 | 제약·준수 점검표 | XLSX·CSV·PDF | 항목·판정·근거·RuleSet·조치, 실제 Cell 근거 |
| R1-M8-04 | R1-M8-01 | 비교·데이터 표 | XLSX·CSV·PDF | 기준·값·차이·누락·충돌, 실제 Cell·Version 일치 |
| R1-M8-05 | R1-M8-01 | 지식 구조도·마인드맵 | JSON·SVG/PNG·PDF | Node·Edge·조건·근거·신뢰 상태, 실제 Open·Render |
| R1-M8-06 | R1-M8-01 | 업무 문서 초안 | DOCX·PDF | Template·Section·근거·편집·검토 상태 |
| R1-M8-07 | R1-M4-07, R1-M8-01, R1-M8-02, R1-M8-03, R1-M8-04, R1-M8-05, R1-M8-06 | 검토·승인·전달 | ReviewRequest, revision_requested→draft, ApprovalRequest, Approval, Delivery, Step-up, 현재 AccessDecision | 승인 요청 기본 7일·조직 1~30일, 24시간 전 알림, 만료·회수 자동 승인 0건, 민감 승인·외부 전달 추가 인증, 권한 축소 차단, Audit, 승인 후 변경 시 재승인 |
| R1-M8-08 | R1-M6-05, R1-M8-07 | 생산 지식 등록 | KnowledgeRegistration, 불변 SourceVersion, 순환 탐지, Step-up·현재 권한 | 명시 등록과 추가 인증, 원본·Run·Model·검토 계보, 권한 축소 후 등록 0건, 자동 Daon 승격 0건 |
| R1-M8-09 | R1-M7-01, R1-M8-02, R1-M8-03, R1-M8-04, R1-M8-05, R1-M8-06, R1-M8-07, R1-M8-08 | Web Studio E2E | R1-WEB-02 | Production Chrome 실제 클릭, 생성 설정·잠금·Snapshot, 5종 파일, Version·Review·Approval·Audit |
| R1-M8-10 | R1-M7-02, R1-M8-01, R1-M8-06 | Windows Offline Studio 초안 | R1-WIN-01 잔여 | 네트워크 차단 상태 생성·편집, 암호화 Queue·RunSnapshot, 재연결 승인 Sync |
| R1-M8-11 | R1-M7-03, R1-M8-02, R1-M8-03, R1-M8-04, R1-M8-05, R1-M8-06, R1-M8-07, R1-M8-08 | Windows 전체 Studio E2E | R1-WIN-03 | 설치 App 실제 클릭, 생성 설정·잠금·Snapshot, 5종 파일, Version·Review·Approval·Audit |
| R1-M8-12 | R1-M4-07, R1-M7-04, R1-M8-07 | Android 간단 편집·검토·승인 | 제목·기존 텍스트·단순 표 Cell·Comment·검토·승인 화이트리스트, 구조·Layout·근거 연결·재생성 차단 | 실기기 허용/거부 Matrix, API 우회 거부, Notification, 제한 Offline 열람, Reconnect·Audit |
| R1-M8-13 | R1-M4-07, R1-M7-05, R1-M8-07 | iOS 간단 편집·검토·승인 | 제목·기존 텍스트·단순 표 Cell·Comment·검토·승인 화이트리스트, 구조·Layout·근거 연결·재생성 차단 | Device/Simulator 허용/거부 Matrix, API 우회 거부, Notification, 제한 Offline 열람, Reconnect·Audit |

### M8 Exit Gate

- 다섯 산출물 모두 실제 파일로 열리고 내용·Layout 검증 통과
- 산출물 유형 선택 후 즉시 생성하지 않고 생성 설정을 확인하며, 조직 강제 RuleSet·검토 조건을 해제할 수 없음
- Version·근거·가중치·RuleSet·Model·편집·검토·승인·Audit 계보 일치
- 승인 후 내용·근거·가중치·모델·RuleSet·생성 설정 변경 시 새 Version·재승인
- 생산 지식의 명시 등록·불변 Version·순환 탐지 검증
- Android·iOS 모바일 편집 화이트리스트 허용/거부 Matrix와 서버 우회 차단 검증
- R1-WEB-02·R1-WIN-01·R1-WIN-03 전체 통과, Android·iOS 여정 잔여 단계 통과

## 19. M9 — 운영 완료와 Release 검증

### 목표

설치·배포·Update·Alarm·Recovery·보안·성능·전체 E2E를 Production-like 환경에서 검증하고 Release 1 최종 승인을 요청한다.

M9의 기본 검증 대상은 격리된 Test 또는 Production-like 환경이다. 외부 사용자가 접근하는 환경으로 배포하려면 실행 직전 G9-DEPLOY 승인 기록이 필요하다. 운영 데이터나 운영 자원을 대상으로 Restore·파괴적 장애 훈련을 하려면 G9-DRILL 승인 기록이 필요하다. 승인 기록에는 정확한 대상, 영향 범위, 실행자, 시작·종료 조건, Backup, Rollback·복구 절차를 포함한다.

| Work Order | depends_on | 단일 목표 | 주요 범위 | 어울2 제출 증거 |
| --- | --- | --- | --- | --- |
| R1-M9-01 | R1-M8-13 | 운영자 정책 흐름 | R1-OPS-01의 정책·권한·Provider·RuleSet·가중치 잠금 | 관리자 실제 클릭, Policy Version·Audit |
| R1-M9-02 | R1-M9-01 | 상태·알림·재처리·복구 | R1-OPS-01의 API·Worker·DB·Object·Model·Node·Connector·Queue 복구와 waiting_model 재큐 | 모델 Ready Event·수동 재처리→현재 정책 새 Run→복구, 중복·폭주 방지 화면·Audit |
| R1-M9-03 | R1-M9-02 | Web·Cloud Release Package | Container·Migration·Production-like 내부 배포·Rollback Runbook | 배포·종료·재기동·Rollback; 외부 환경은 G9-DEPLOY 선행 |
| R1-M9-04 | R1-M9-02 | Windows 서명 Package·Update | 서명 Installer, Local Service, Update·Rollback | 설치·종료·재기동·Update·Rollback 실제 증거 |
| R1-M9-05 | R1-M9-02 | Android 서명 Build·Update | 서명 APK/AAB, 설치·권한·Update | 실제 Android Device 설치·Update·Rollback/복구 |
| R1-M9-06 | R1-M9-02 | iOS 서명 Build·Update | 승인된 macOS Build Host/CI Runner, 고정 Xcode·CocoaPods·RN Toolchain, Apple Team·Signing·Provisioning, Archive/설치 Build·Update | Host·Toolchain·Team·Signing·Provisioning 증거, 실제 Device/Simulator 설치·Update·복구 |
| R1-M9-07 | R1-M5-07, R1-M9-02 | Backup·Restore·재해 복구 | 격리된 Cloud·Local Restore, RTO/RPO 훈련 | 복구 후 권한·계보·Audit; 운영 대상은 G9-DRILL 선행 |
| R1-M9-08 | R1-M9-03, R1-M9-04, R1-M9-05, R1-M9-06, R1-M9-07 | 보안·개인정보 검증 | Tenant, RLS, Secret, Egress, Prompt Injection, Tool 권한, 민감 작업 Step-up, 과거 결과 현재 ACL | 교차 접근·Secret 노출·무단 전송·추가 인증 우회·권한 철회 후 과거 결과 노출 0건, 마스킹/차단·새 Run Audit |
| R1-M9-09 | R1-M9-08 | 성능·용량·비용 검증 | 파일·Index·Run·동시 사용자·Local HW·Provider 비용·비용 한도 차단 | M0 SLO·한도 충족, Preflight/실행 중 COST_LIMIT_EXCEEDED와 정책 변경 후 새 Run, 병목·축소 운영 보고 |
| R1-M9-10 | R1-M9-03, R1-M9-04, R1-M9-05, R1-M9-06, R1-M9-09 | 접근성·반응형·플랫폼 호환성 | Web·Windows·Android·iOS 지원 기준 | 키보드·Screen Reader·폭별 Layout·지원 OS/Browser Matrix |

M9의 전체 회귀와 승인은 개발 Work Order가 아니라 다음 Verification Activity와 Gate로 분리한다.

| Verification Activity | 소유자 | depends_on | 산출물 |
| --- | --- | --- | --- |
| R1-M9-V01 | 어울1·검증 담당 | R1-M9-01, R1-M9-02, R1-M9-03, R1-M9-04, R1-M9-05, R1-M9-06, R1-M9-07, R1-M9-08, R1-M9-09, R1-M9-10 | 8개 R1 여정의 실제 Browser·EXE·APK·iOS Build E2E Matrix와 Evidence Manifest |
| R1-M9-V02 | 어울1 | R1-M9-V01 | Dependency 독립성 재감사, 전체 증거 Pack·Checksum, 미해결 Blocker 0 확인 |

### M9 Exit Gate

- R1-M9-01~10에 대한 어울2 `COMPLETED` 보고를 어울1이 대조하고 Milestone을 `VERIFYING`으로 전환
- R1-M9-V01·V02 완료 후 R1-WEB-01·02, R1-WIN-01·02·03, R1-AND-01, R1-IOS-01, R1-OPS-01 모두 통과
- Local-private와 Cloud-sync 각각 검증
- Local·Internal·External 모델 선택과 무단 Fallback 방지 검증
- Daon 연결·미연결·장애·재연결 검증
- 권한·Tenant 격리·외부 전송·Audit·Backup·Restore 검증
- 미해결 Blocker 0건
- 최신 설계·계획·최종 Diff와 증거 Pack을 CLAUDE에게 현재의 독립 방식으로 전달하고 G9-INDEPENDENT 결과 수집
- CLAUDE 결과·남은 위험·예외 0건을 신산님에게 보고하고 별도 G9-RELEASE 최종 완료 승인

---

## 20. R1 사용자 여정 추적표

| 여정 ID | 주 담당 Work Order | 필수 선행 Work Order | 최종 증거 |
| --- | --- | --- | --- |
| R1-WEB-01 | R1-M7-01 | G2-UX, R1-M3-01, R1-M4-01, R1-M4-03, R1-M4-04, R1-M4-05, R1-M5-01, R1-M5-02, R1-M5-04, R1-M6-01, R1-M6-02, R1-M6-05, R1-M6-06, R1-M6-08, R1-M6-09, R1-M6-10, R1-M6-11, R1-M6-12, R1-M6-13, R1-M6-14, R1-M6-15, R1-M6-16 | 파일·직접 입력·인터넷·Daon·가중치, Production Chrome, Network·Console, RunSnapshot |
| R1-WEB-02 | R1-M8-09 | R1-M7-01, R1-M8-01, R1-M8-02, R1-M8-03, R1-M8-04, R1-M8-05, R1-M8-06, R1-M8-07, R1-M8-08 | GenerationSettingsSnapshot, 5종 파일, Version·Review·Approval·Audit·KnowledgeRegistration |
| R1-WIN-01 | R1-M7-02, R1-M8-10 | G2-UX, R1-M3-02, R1-M3-03, R1-M4-02, R1-M4-03, R1-M4-04, R1-M4-06, R1-M5-03, R1-M5-04, R1-M5-05, R1-M6-01, R1-M6-02, R1-M6-03, R1-M6-04, R1-M6-05, R1-M6-06, R1-M6-07, R1-M6-08, R1-M6-09, R1-M6-10, R1-M6-14, R1-M6-15, R1-M6-16 | EXE, Loopback·IPC, 외부 연결 0, Local ASR·검색·질문·근거·Studio 초안·암호화 Queue·승인 Sync |
| R1-WIN-02 | R1-M7-03 | R1-M3-02, R1-M4-05, R1-M5-05, R1-M6-10, R1-M6-12, R1-M6-13, R1-M6-14, R1-M6-16 | Route·Model·Network·EgressDecision·Audit |
| R1-WIN-03 | R1-M8-11 | R1-M7-03, R1-M8-01, R1-M8-02, R1-M8-03, R1-M8-04, R1-M8-05, R1-M8-06, R1-M8-07, R1-M8-08 | 실제 App 클릭, GenerationSettingsSnapshot, 5종 파일, Version·Review·Approval·Audit |
| R1-AND-01 | R1-M7-04, R1-M8-12 | R1-M3-04, R1-M3-05, R1-M4-03, R1-M4-04, R1-M4-05, R1-M4-07, R1-M5-04, R1-M5-05, R1-M6-04, R1-M6-05, R1-M6-07, R1-M6-10, R1-M6-14, R1-M6-15, R1-M6-16 | APK·실기기, 권한·오디오 의미 이해·ASR 계보, 모바일 편집 허용/거부 Matrix, Background·Notification·Offline·Reconnect·Trace·Audit |
| R1-IOS-01 | R1-M7-05, R1-M8-13 | R1-M3-04, R1-M3-06, R1-M4-03, R1-M4-04, R1-M4-05, R1-M4-07, R1-M5-04, R1-M5-05, R1-M6-04, R1-M6-05, R1-M6-07, R1-M6-10, R1-M6-14, R1-M6-15, R1-M6-16 | macOS Host·Xcode·Signing·Provisioning, Archive/설치 Build, Device/Simulator, 권한·오디오 의미 이해·ASR 계보, 모바일 편집 허용/거부 Matrix, Background·Notification·Offline·Reconnect·Trace·Audit |
| R1-OPS-01 | R1-M9-01, R1-M9-02 | R1-M2-06, R1-M2-07, R1-M4-02, R1-M4-03, R1-M4-04, R1-M4-07, R1-M5-07, R1-M6-03, R1-M6-04, R1-M6-10, R1-M6-11, R1-M6-12, R1-M6-13, R1-M6-14, R1-M6-16 | 관리자 실제 클릭, Policy Version·Audit, 장애→경고→재처리→복구 |

위 표는 계획서 수준의 최소 선행 관계다. M0의 실행 추적표와 개별 Work Order 패킷에서는 범위 약어를 사용하지 않고 모든 `depends_on` ID, 승인 Gate, 완료 Evidence Manifest를 완전히 전개한다.

### 20.1 상세 설계 Coverage 요약

| 상세 설계 범위 | 주 담당 Milestone·계획 조항 |
| --- | --- |
| §1~§3 제품 정의·목표·불변 원칙 | M0, M1, 본 계획 §5~§7 |
| §4~§6 Client·3면 Workspace·데이터 영역 | M2, M3, M5, M7 |
| §7~§9 지식 권위·Source·Retrieval·근거 | M6, M7 |
| §10~§12 선택형 LLM·Local LLM·Run | M6, M7 |
| §13 Studio | M8 |
| §14 계정·조직·권한·장치 | M2, M4, M6, M9 |
| §15~§17 아키텍처·데이터 정본·API | M1, M3, M4, M5 |
| §18~§21 상태·Connector·보안·운영 | M2, M4, M5, M6, M7, M9 |
| §22 기술 구성·배포 | M0, M1, M3, M9 |
| §23~§25 Release·Milestone·완료 증거 | M0~M9, CP1~CP5·RC, 본 계획 §8~§26 |
| §26 개발 Subagent 운영 | 본 계획 §22~§24와 `$daon-subagent-delivery` Skill |
| §27~§28 결정·설계 결론 | M0 결정 기록과 전체 Release Gate |

## 21. 검증과 증거 관리

### 21.1 증거 수준

각 Work Order 보고서는 다음을 구분한다.

1. 정적 확인: Lint, Type, Schema, 금지 패턴, Diff
2. Build: 실제 App·Service·Installer·Archive Build
3. 자동 테스트: Unit, Contract, Integration, Migration, Security, E2E
4. 실제 화면·운영 검증: Browser·EXE·Device 클릭, Network, Process, 장애·복구

앞 단계가 통과해도 필요한 실제 화면·운영 검증이 없으면 `COMPLETED`가 아니다.

### 21.2 영역별 최소 증거

| 영역 | 필수 증거 |
| --- | --- |
| 독립성 | 정상 Git, Dependency Graph, Daon 직접 의존·Connector 우회 0건 |
| Web | Production Process, 실제 Chrome 클릭, Network·Console, 반응형 |
| Windows | 설치 EXE, Process·IPC·Loopback, Offline·Reconnect, Update·Rollback |
| Android | APK 설치, 실제 Device 클릭, 파일·사진·음성·권한·ASR·Background·Notification·Offline·Reconnect·Trace·Audit |
| iOS | 승인된 macOS Build Host/CI Runner, 고정 Xcode·CocoaPods·RN Toolchain, Apple Team·Signing·Provisioning, Archive/설치 Build, Device/Simulator 클릭, 파일·사진·음성·권한·ASR·Background·Notification·Offline·Reconnect·Trace·Audit |
| API | 실제 Process, Auth·Error·Idempotency·Concurrency·Graceful Shutdown |
| Cloud Data | Migration·Transaction·Vector·Object·Backup·Restore |
| Local Data | 암호화·Restart·Vector 검색·손상 복구·Key Revoke |
| Source | 원본·전사·Page·Cell·Region·Time·Index·재처리 계보 |
| LLM | UI 선택과 Route·Model·Network·Egress·Lineage 일치 |
| Daon·RuleSet | Version·Auth·Timeout·Retry·Disconnect·Snapshot·만료·차단 |
| Studio | 실제 DOCX·PDF·XLSX·CSV·JSON·SVG·PNG Open·내용·Layout |
| 운영 | 화면 상태·경고·재처리·Update·Rollback·Backup·Recovery |

### 21.3 증거 저장 규칙

- Work Order: `docs/02_work_orders/release_1/<work_order_id>.md`
- 작업보고서: `docs/04_test_reports/release_1/<work_order_id>_report.md`
- 진행 복구 기록: `docs/04_test_reports/release_1/<work_order_id>_progress.md`
- 증거 Pack: `docs/03_evidence/release_1/<work_order_id>/`
- 증거 Manifest: `docs/03_evidence/release_1/<work_order_id>/manifest.json`
- 결정 기록: `docs/01_architecture/DECISIONS.md`
- 추적표: `docs/02_work_orders/release_1_traceability.md`
- 승인 기준 Manifest: `docs/02_work_orders/release_1_baseline_manifest.json`
- 실행·실패 Ledger: `docs/02_work_orders/release_1_attempt_ledger.jsonl`

Baseline Manifest는 자기 참조 Hash를 계획서 안에 기록하는 대신 승인 시점의 계획 Hash와 설계·결정·추적표 Hash, 기준 Commit, 승인 기록을 외부에서 고정한다. Attempt Ledger는 `work_order_id`, `issue_id`, `attempt`, 실행 Agent, 시작·종료 시각, 보고 상태, 어울1의 재분류·유효성 판단, 보고서 경로를 Append-only로 누적한다.

진행 복구 기록은 어울2가 직접 유지하는 필수 산출물이다. Work Order 착수, 각 세부 단계 완료, 오류 발생·원인 확인·복구, 각 테스트 실행, 결과보고 제출과 Agent 종료 직전에 갱신한다. 각 항목은 `recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`를 포함한다. `INCOMPLETE` 복구는 이 파일의 마지막 `COMPLETED` 단계부터 이어서 수행하며 작업보고서는 진행 기록 경로와 최종 Hash를 포함한다.

화면 캡처만으로 판정하지 않는다. 캡처는 실행 시각·Build/Commit·Actor·Trace/Run ID·Network·Audit와 연결한다. Evidence Manifest는 파일별 SHA-256, 생성 명령·환경·Build/Commit을 기록한다. 비밀값·개인정보·원문 민감 자료는 증거에서 Masking한다.

### 21.4 테스트 정리

- Test Fixture는 고유 ID와 전용 Tenant/Workspace를 사용한다.
- 기존 사용자·원본 자료를 수정하거나 삭제하지 않는다.
- Child-first 정리와 잔존 데이터 0건을 확인한다.
- 기동한 Process·Port·Local Node는 검증 후 정상 종료한다.
- 삭제·복구·Update·Rollback은 정확한 대상과 복구 절차를 사전 확인한다.

## 22. Work Order 준비·완료 기준

### 22.1 Definition of Ready

Work Order는 다음을 모두 충족해야 `READY`가 된다.

- 승인된 상세 설계서와 계획서 경로·버전·Hash 확인
- 연결된 R1-D 결정의 확정 상태, 결정 기록과 추적 ID 확인
- 개별 Work Order 문서와 승인된 Baseline Manifest 존재
- 하나의 계약 또는 사용자 흐름으로 범위 제한
- 연결 설계 조항·Milestone·R1 여정 ID 지정
- 모든 `depends_on` Work Order·Gate의 완료 Evidence Manifest 확인
- 현재 Branch·Commit·Worktree·선행 Diff와 기존 Dirty 상태 확인
- 지정 Writer·작업 Claim 시각과 동일 범위 다른 Writer 0명 확인
- 허용 변경 파일·모듈과 제외 범위 지정
- 유지할 기존 기능·외부 동작·불변 조건 지정
- API·데이터·보안·배포 영향과 승인 경계 지정
- 실행 가능한 테스트와 실제 증거 수집 방법 준비
- 필요한 Credential·Sandbox·서명 계정·실기기 준비
- C3 작업이면 승인 기록 ID, 정확한 대상, Rollback·Cleanup 절차 준비
- Evidence Manifest와 Attempt Ledger 경로 준비

### 22.2 Definition of Done

Work Order는 다음을 모두 충족해야 `COMPLETED`다.

- 어울2의 `COMPLETED` 보고에 필수 산출물과 변경 파일 목록 존재
- 완료 조건별 근거와 테스트 결과, Evidence Manifest·Checksum 존재
- 정적 확인·Build·자동 테스트·실제 화면/운영 검증 구분
- 관련 회귀와 기존 기능 유지 증거 존재
- 데이터 Fixture·Process·Port 정리 완료
- 문서·OpenAPI·Migration·결정·추적표 동기화
- 안전·권한·Secret·외부 전송 검사 통과
- 허용 범위 밖 Diff 0건, 결과가 기준 Commit·Build와 연결됨
- 미해결 사항 0 또는 신산님의 change/approval ID가 있는 승인 예외
- 계약 변경의 Downstream 재검증 범위와 수락자·수락 시각 기록

어울2의 `COMPLETED`는 개발 결과보고 상태이며 신산님의 최종 완료 승인이 아니다. 어울1이 보고를 계획과 대조해 기술 증거가 충분하면 Work Order 또는 Milestone을 `VERIFYING`으로 전환한다. 필요한 독립 검증과 Gate가 끝난 뒤에만 계획 상태를 `COMPLETED`로 확정한다.

## 23. 어울2 작업 패킷

M0 Baseline Activity, G2/G9 승인 Gate와 M9 Verification Activity는 개발 Subagent 대상이 아니다. M1~M9의 개발 Work Order 시작 시에만 어울1은 `$daon-subagent-delivery` Skill을 사용해 다음 전체 패킷을 어울2에게 전달한다.

```text
work_order_id:
issue_id:
작업지시서 경로·버전·Hash:
depends_on 완료 Evidence Manifest:
목표와 사용자 관점 완료 조건:
승인된 상세 설계서 전체 경로·버전·Hash:
승인된 작업계획서 전체 경로·버전·Hash:
승인 기준 Manifest 경로·Hash:
연결 Milestone·R1 여정·설계 조항:
이번 포함 범위:
이번 제외 범위:
허용 변경 파일·모듈:
보존할 기존 기능·외부 동작:
API·데이터·보안·배포 승인 경계:
수행할 테스트:
필수 완료 증거와 저장 경로:
현재 Git 상태·Diff·선행 변경:
현재 Branch·Commit·지정 Writer·Claim 시각:
알려진 위험·미해결 사항:
C3 승인 기록 ID와 Rollback·Cleanup:
Evidence Manifest·Attempt Ledger 경로:
진행 복구 기록 경로·필수 갱신 시점:
구현 중 발견 사항을 어울1에게 되돌리는 조건:
```

`work_order_id`는 계획 작업 단위다. 최초 문제의 `issue_id`는 `<work_order_id>-I001` 형식으로 만들고, 같은 문제의 재작업에서는 유지하며 별개 원인은 다음 번호를 사용한다.

요약본으로 상세 설계서와 계획서를 대체하지 않는다. 어울2는 두 문서를 EOF까지 읽고 Baseline Manifest의 Hash와 일치함을 확인하며, 적용할 설계 조항과 계획 조항을 보고하기 전에는 쓰기 작업을 시작하지 않는다.

작업지시 프롬프트는 위 패킷을 다시 서술하지 않는다. `AGENTS.md`, 승인 설계·계획, Baseline Manifest와 지정 작업지시서를 EOF까지 읽고 Hash를 확인한 뒤 단일 Writer로 수행하라는 명령, 진행 복구 기록 의무와 결과 계약만 포함한다.

## 24. 결과 분류·재작업·인수

모든 종료 보고는 다음 필드를 포함한다.

```text
status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단
```

| 상태 | 판정 |
| --- | --- |
| `COMPLETED` | 산출물·변경 파일·완료 조건별 근거·테스트가 모두 존재 |
| `FAILURE_REPORT` | 동일 issue_id, 실패 단계·확인 원인·오류/테스트/코드 증거·현재 변경·남은 작업·필요 판단이 모두 존재 |
| `INCOMPLETE` | 결과보고 미완성 또는 Tool/Session의 예기치 않은 종료 |
| `BLOCKED` | 권한·환경·설계 판단 또는 신산님의 결정 필요 |

Agent 실행의 `Done` 표시는 `COMPLETED` 증거가 아니다. 형식만 `FAILURE_REPORT`이거나 필수 근거가 없으면 실패 횟수에 추가하지 않고 어울1이 `INCOMPLETE` 또는 `BLOCKED`로 재분류한다. 모든 원보고와 재분류 근거를 Attempt Ledger에 Append-only로 남긴다.

재작업 규칙:

1. 같은 `issue_id`의 유효한 `FAILURE_REPORT` 1·2회는 어울1이 지시를 보완해 같은 어울2에게 재전달한다.
2. 두 번째 실패 후 구현 방법이 바뀌면 설계·계획·결정 기록을 코드보다 먼저 갱신한다.
3. 동일 작업지시서의 `FAILURE_REPORT` 또는 `INCOMPLETE` 원보고 합계가 3회이거나 같은 문제의 유효한 세 번째 실패이면 어울2의 쓰기를 중지한다.
4. 원보고, 진행 복구 기록, 현재 Diff, 변경 파일, 테스트와 남은 작업을 회수하고 유효한 실패 횟수와 미완료 횟수를 구분한다.
5. 신산님에게 보고해 직접 구현 여부의 결정을 받는다.
6. 신산님 승인 후에만 `DIRECT_IMPLEMENTATION`을 선언하고 어울1이 직접 구현한다.
7. `INCOMPLETE`와 `BLOCKED`는 유효한 실패 횟수에 포함하지 않는다. 단, `INCOMPLETE`는 동일 작업지시서의 의무 보고 임계값에는 포함한다.
8. 합계 3회 미만의 `INCOMPLETE`는 진행 복구 기록의 마지막 성공 단계부터 같은 어울2를 재개한다. 재개할 수 없으면 후속 어울2에게 원래 전체 패킷과 현재 상태를 전달한다.
9. `BLOCKED`는 권한·환경·설계 판단·신산님 승인 중 원인을 분류한다. 승인 범위 안의 기술 판단은 어울1이 처리하고 C2/C3만 신산님에게 요청한다.

보완 판정 규칙:

- `MAJOR_REWORK`: 필수 여정 차단, 설계·보안·데이터 계약 위반, 허위·불충분 핵심 증거 또는 회귀. 별도 수정 작업지시서를 발행한다.
- `MINOR_FOLLOWUP`: 합격 조건을 깨지 않는 문구·표시·경미한 증거 보완. 현재 작업을 다시 열지 않고 다음 Work Order에 명시적으로 흡수한다.
- 모든 검토 보고는 `판정 → 판단 이유 → 조치` 순서로 작성한다.

결과 수락 순서는 다음과 같다.

```text
어울2 COMPLETED 보고
→ 어울1이 계획·증거·Diff 대조
→ Work Order 또는 Milestone VERIFYING
→ 계획에 지정된 회귀·독립 검증·승인 Gate
→ 어울1의 기술 수락 또는 신산님의 최종 승인
→ 계획 상태 COMPLETED
```

CLAUDE 독립 검증은 어울2의 `COMPLETED` 보고 뒤에 수행한다. 외부 독립 검증을 어울2의 결과보고 선행조건으로 두지 않는다.

## 25. 초기 Risk Register

| Risk ID | 위험 | 영향 | 초기 대응 | Gate |
| --- | --- | --- | --- | --- |
| R1-R001 | Web·Windows·Android·iOS 동시 범위 | 일정·회귀 확대 | M2 공통 UX 승인, 계약·Token 공유, 플랫폼 E2E 분리 | M2·M3 |
| R1-R002 | Local-private·Cloud-sync 이중 정본 | 유출·충돌·데이터 손실 | 영역 분리, 승인 Copy/Publish, 불변 Version, Reconnect 충돌 | M5 |
| R1-R003 | 모델 자동 선택·Fallback·비용 한도·waiting_model 재큐 | 무단 외부 전송·재현 불가·중복 실행·비용 폭주 | Hard Filter, Frozen Policy, EgressDecision, ModelAttempt, COST_LIMIT_EXCEEDED, 새 ProcessingRun·Idempotency·Backoff | M6·M9 |
| R1-R004 | 강제 RuleSet 가용성 | 잘못된 허용·전체 장애 | 검증 Snapshot, 적용 Run만 fail-closed, Binding 없는 기능 보존 | M6·M7 |
| R1-R005 | Daon Sandbox·계약 접근 지연 | Connector·R1-WIN-02 차단 | M0 자격·버전 확인, Simulator가 아닌 공식 Sandbox 준비 | M0·M6 |
| R1-R006 | Local LLM 하드웨어·라이선스 편차 | 설치 실패·품질 편차 | Hardware 진단, Allowlist, Digest·License, Rollback | M0·M6 |
| R1-R007 | Vision/LLM 의미 이해와 Parser·OCR·ASR 추출의 형식·결과 편차 | 문맥 오해·근거 위치·ASR-only 완료 | Vision/LLM-first, 오디오 직접 이해 또는 ASR+LLM, 보조 추출 교차 검증, 원본·불일치·검토 Version 보존, Parser/ASR-only 완료 금지 | M6·M7 |
| R1-R008 | Studio Export Layout 불일치 | 업무 파일 사용 불가 | 실제 Office/PDF/SVG Open·Render 검증 | M8 |
| R1-R009 | macOS Build Host·Xcode·Apple Signing, Mobile 실기기·알림 계정 미확보 | iOS Archive와 실제 증거 수집 차단 | M0에서 macOS Host/Runner·Toolchain·Team·Provisioning·Device·권한 확보, 미확보 시 iOS Work Order `BLOCKED` | M0·M3·M9 |
| R1-R010 | Tenant·Egress·Secret·민감 작업·권한 변경 보안 결함 | 추가 인증 우회·권한 철회 후 과거 결과 노출·중대 개인정보 위험 | RLS+Service Auth, StepUpAuthorization, 현재 ACL AccessDecision, 파생부 마스킹/차단, Security Gate | M4·M5·M8·M9 |
| R1-R011 | 초기 Git 기준선 부재 | 변경 추적·복구 불가 | M0 문서 기준 Commit과 Manifest 고정 후 M1 개발 기준선 승계 | M0·M1 |
| R1-R012 | M2 UI 폐기·재작성 또는 Mock이 실제 기능으로 잔존 | G2 승인 화면 괴리·허위 완료·중복 일정 | Production-bound 재사용 계약, Mock Adapter 격리, M3 전환표와 화면 회귀 | M2·M3·M9 |
| R1-R013 | 첫 실제 수직 통합이 늦어짐 | M4~M6 대규모 구현 후 통합 실패 발견 | R1-M6-10 CP3 Web Thin Vertical E2E 통과 전 추가 형식·Provider·Connector 확장 중지 | M6 |

## 26. Release 1 최종 완료 조건

Release 1은 다음을 모두 만족하고 신산님이 승인해야 완료다.

- 모든 핵심 작업을 화면으로 수행
- 8개 필수 Client 여정 실제 동작
- Local-private와 Cloud-sync 별도 검증
- Local·Internal·External 모델과 무단 Fallback 방지 검증
- Vision/LLM-first 문서 이해, Parser·OCR 검증·보완 전용 역할과 Parser-only 완료 0건, 오디오 직접 이해/ASR+LLM Ready Gate와 ASR-only 완료 0건 검증
- Daon 연결·미연결·장애·재연결 검증
- 다섯 지식 유형·권위·가중치 범위·계층 우선순위·Clamp와 중요 충돌 판정·검토 차단 검증
- `waiting_model` 제한 자동·수동 새 ProcessingRun 복구와 중복 억제, `COST_LIMIT_EXCEEDED` 종료·새 Run 검증
- 생성 설정·잠금·GenerationSettingsSnapshot과 다섯 Studio 산출물의 실제 파일 Open·전체 계보 검증
- 모바일 편집 화이트리스트와 서버 우회 차단 검증
- 민감 작업 Step-up, 권한 축소 후 과거 결과 현재 ACL 마스킹·차단·현재 정책 새 Run 검증
- 권한·Tenant 격리·외부 전송·Audit·Backup·Restore 검증
- Daon 내부 직접 의존과 Connector 우회 0건
- 미해결 Blocker 0건
- 최신 설계·계획·Diff에 대한 CLAUDE 독립 검증 완료
- 신산님의 G9-RELEASE 최종 완료 승인

## 27. 착수 시 첫 실행 순서

신산님이 상세 설계서와 본 계획을 승인한 뒤 다음 순서로 진행한다.

1. 어울1이 R1-M0-A01 문서·승인 기준선을 고정
2. 어울1과 신산님이 R1-M0-A02 핵심 결정·Risk Register를 확정
3. 어울1이 R1-M0-A03 전체 추적표를 작성
4. 어울1과 신산님이 R1-M0-A04 환경·증거·작업보고 기준선을 확정
5. 신산님이 G0-BASELINE을 승인하고 Baseline Manifest를 고정
6. R1-M1-01 Git 기준선 수립
7. 이후 `depends_on`이 완료된 개발 Work Order를 한 건씩 어울2에게 전달

G0-DESIGN·G0-PLAN·G0-BASELINE은 각각 `APR-G0-DESIGN-20260720-01`, `APR-G0-PLAN-20260720-01`, `APR-G0-BASELINE-20260720-01`로 승인되었다. 구현 상태는 `READY`이며 다음 실행 단위는 `R1-M1-01`이다. R1-D004·D006·D007·D008·D011·D012와 연결된 Work Order는 필요한 외부 환경이 확보될 때까지 조건부 `BLOCKED`를 유지한다.

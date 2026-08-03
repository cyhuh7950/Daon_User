# Daon 사용자형 지식 업무지원 프로그램 Release 1 테스트·검증 계획서

## 문서 정보

| 항목 | 내용 |
| --- | --- |
| 문서 구분 | Release 1 테스트·검증 계획 정본 |
| 계획 ID | `DAON-USER-R1-TEST-PLAN` |
| 계획 버전 | `0.9` |
| 작성일 | 2026-07-20 |
| 상태 | 승인 · 신산님 · 2026-07-20 |
| 승인 기록 | `APR-TP0-20260720-01` |
| 대상 제품 | Daon 사용자형 지식 업무지원 프로그램 |
| 대상 Release | Release 1 — 핵심 업무형 |
| 검증 기준 설계서 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` |
| 검증 기준 작업계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` |
| 테스트·검증 담당 | CLAUDE · 외부 독립 검증자 겸 테스트 담당 |
| 설계·기술 책임자 | 어울1 |
| 개발 수행자 | 어울2 (`daon-developer`) |
| 최종 승인자 | 신산님 |

> 이 계획서는 승인된 상세 설계서와 Release 1 작업계획서를 검증 기준으로 사용한다. 두 기준 문서가 변경 통제 절차(작업계획 §3)에 따라 갱신되면, 이 계획서의 해당 검증 항목도 함께 갱신한다.

### 변경 이력

| 버전 | 일자 | 변경 내용 | 승인 상태 |
| --- | --- | --- | --- |
| 0.1 | 2026-07-20 | 최초 작성 | 승인 대기 |
| 0.2 | 2026-07-20 | 테스트 실행 시점을 작업계획 Gate 기준 7개 웨이브(TP-0·TP-1·TP-2A·TP-2·TP-3·TP-4·TP-5)로 통합, 매 Milestone 테스트 제거, §6에 실행 웨이브 태그 추가 | 승인 대기 |
| 0.3 | 2026-07-20 | Vision/LLM-first·Parser/OCR 보조 계약, Studio 생성 설정, Production-bound M2, CP3 초기 Web Thin Vertical E2E와 macOS Build Gate 검증 추가 | 승인 대기 |
| 0.4 | 2026-07-20 | CP3 Core/확장 Work Order 분리, 정책 차단·Runtime 부재·부분 이해 상태와 Studio 승인 요청 수명주기 검증 보강 | 승인 대기 |
| 0.5 | 2026-07-20 | 개정 설계서 재검토 반영: CP↔TP 매핑표(§1.5) 추가, 시나리오 6종 v0.4 정합화(02 Vision/LLM Fallback 교정), 설계 질의서 분리(N1~N3 신규) | 승인 대기 |
| 0.6 | 2026-07-20 | 설계 질의 Q1~Q3·Q7·Q8·N1~N3 전량 해소 반영: 시나리오 v0.5로 확정 계약(중요 충돌 severity·가중치 0.5~2.0·COST_LIMIT_EXCEEDED·오디오 2경로·waiting_model 재처리·Step-up·현재 권한 AccessDecision) 채움, 질의서 종결 | 승인 대기 |
| 0.7 | 2026-07-20 | TP-0 Baseline 예외, TP-2A M6 중간 진입, CP1 매핑, TP-5 선행조건, 웨이브 보고 경로·결정권자 정합화 | 신산님 승인 |
| 0.8 | 2026-08-04 | CP3 Go 실행 결정 반영, Upstage `solar-pro3` 의미 이해·생성 및 `document-parse` 검증 경로 고정, M5~M8 증거·Milestone Exit 소급 검증과 `CONTRACT_COMPLETE / JOURNEY_UNVERIFIED` 추적 추가 | 신산님 승인 · `APR-CP3-GO-20260804-01` |
| 0.9 | 2026-08-05 | Provider 독립 구조·다중 LLM 후보·화면 기반 Provider/Model 선택·Secret 전용 `.env` 계약 반영 | 신산님 승인 |

---

## 1. 목적과 역할

### 1.1 목적

이 문서는 Release 1 개발 결과에 대한 테스트와 검증의 범위, 기준, 절차, 산출물을 고정한다. 검증의 목표는 다음 세 가지다.

1. **설계 적합성**: 구현이 승인된 상세 설계서의 조항대로 개발되었는지 확인한다.
2. **기능 정확성**: 각 Work Order의 완료 조건과 R1 필수 여정이 실제 환경에서 동작하는지 확인한다.
3. **불변 조건 보존**: 작업계획 §6의 15개 구현 불변 조건이 어떤 변경 후에도 깨지지 않는지 확인한다.

### 1.2 역할 경계

- 테스트 담당(CLAUDE)은 **읽기 전용 검증자**다. 제품 코드를 수정하지 않으며, 결함을 발견해도 직접 고치지 않는다.
- 테스트 담당의 결함 보고는 어울2의 `FAILURE_REPORT` 횟수에 포함하지 않는다. 결함은 어울1에게 전달되고, 어울1이 판정·분류하여 재작업 지시 또는 신산님 승인 요청으로 처리한다.
- 어울2가 Work Order 안에서 수행하는 자체 테스트(Unit·Contract·Integration)는 개발 완료 증거이며, 이 계획의 독립 검증을 대체하지 않는다.
- 최종 완료 판정은 신산님의 권한이다. 테스트 담당은 판정 근거를 제공한다.

### 1.3 테스트 실행 시점 원칙

테스트 담당의 독립 테스트는 **매 Milestone마다 실행하지 않는다.** 작업계획서의 검증은 두 층으로 나뉜다.

1. **1차(어울1) Milestone Exit 검증**: 모든 Milestone(M1~M9)에서 어울2의 `COMPLETED` 보고를 어울1이 계획·증거·Diff와 대조하여 `VERIFYING`으로 전환하는 개발 측 검증. 이것은 매 Milestone에서 일어난다.
2. **2차(테스트 담당) 독립 테스트 웨이브**: 검증 가치가 하나의 흐름으로 누적되는 소수 지점에서만 실행한다. 중간 Milestone(M1·M3·M4·M5)의 산출물은 독립 웨이브를 별도로 두지 않고 다음 웨이브에 흡수한다.

이렇게 나누는 이유: 개별 Milestone은 그 자체로 사용자 관점의 완결된 흐름이 아니어서 독립 테스트의 대상이 얇고 반복 비용만 크다. 안전 계약(권위·Fallback·RuleSet·Egress)과 여정은 엔진·데이터·클라이언트가 수직으로 연결된 뒤에야 의미 있게 검증된다.

### 1.4 테스트 웨이브 (작업계획 시점 기준)

| 웨이브 | 작업계획 실행 시점 | 선행 완료 | 테스트 담당 실행 내용 | 대상 시나리오 |
| --- | --- | --- | --- | --- |
| **TP-0 문서 검증** | G0-DESIGN 승인 전 | 설계서·계획 초안 | 설계 적합성 문서 검토, 시나리오 개요의 미확정 Q 항목 회부 | 코드 없음 — 문서만 |
| **TP-1 화면 적합성** | M2 Exit → G2-UX | R1-M2-08 | 화면·흐름 설계 적합성, 반응형·상태 보존, Mock 위장 여부. 신산님 G2-UX 판단용 기술 의견서 제출 | TS-OPS-050·051, TS-KNW/STU 화면 존재 확인(백엔드 미검증), M2 Exit 항목 |
| **TP-2A 초기 수직 E2E** | R1-M6-10 CP3 | R1-M6-10 | 단일 승인 모델·고정 Route·자동 Fallback 없음 조건에서 실제 Web·Auth·DB·Object·Vision/LLM·Parser/OCR 검증·Index·질문·Citation을 단일 PDF로 조기 통합 검증 | CP3, TS-SRC-015·016·017A~C·018·020, INV 14·15 |
| **TP-2 엔진 계약** | M6 Exit | R1-M6-01~16 | 지식 권위·가중치·충돌·RuleSet·Routing·Fallback·Source 처리·근거·ASR·Connector 핵심 결정표. API/서비스 수준(L3~L4). 데이터·보안 불변 조건 Spot-check(Tenant·Egress·Secret·독립성) | TS-KNW 전체, TS-MDL 전체, TS-SRC-019 포함 나머지 시나리오, INV 1·3·4·5·6·7·8·9·10·11·14·15 |
| **TP-3 수직 흐름** | M7 Exit | R1-M7-06 | 실제 파일·실제 클라이언트 E2E. 근거 위치 재현, Local-private 오프라인, 축소 운영 회귀 | TS-SRC 근거·ASR, R1-WEB-01·WIN-01·WIN-02·AND-01(일부)·IOS-01(일부), TS-OPS 축소 운영 |
| **TP-4 Studio** | M8 Exit | R1-M8-13 | 5종 산출물 실제 파일 Open·Layout, 버전·검토·승인·재승인·전달·생산 지식 등록 | TS-STU 전체, R1-WEB-02·WIN-03·AND-01·IOS-01(잔여) |
| **TP-5 최종 독립 검증** | R1-M9-V01·V02 후 G9-INDEPENDENT | R1-M9-V01·R1-M9-V02 | 전체 회귀, 보안 전 스위트, 운영·배포·복구, 8개 여정 E2E, 불변 조건 15항 전수. G9-RELEASE 근거 보고서 | TS-SEC 전체, TS-OPS 전체, 전 영역 회귀, INV 1~15 |

**독립 웨이브를 두지 않는 Milestone과 처리:**

| Milestone | 1차 검증(어울1) | 테스트 담당 흡수 웨이브 |
| --- | --- | --- |
| M1 독립 저장소 | 어울1 M1 Exit | TP-2에서 독립성·CI 재확인, TP-5에서 재감사 |
| M3 Client Shell | 어울1 M3 Exit | TP-3(실행형 클라이언트로 여정 실행 시 함께) |
| M4 API·인증 | 어울1 M4 Exit | TP-2(계약·인증·권한 API 검증), TP-5(보안 전 스위트) |
| M5 Local·Cloud Data | 어울1 M5 Exit | TP-2(암호화·격리 Spot-check), TP-5(Backup·Restore 정식) |

> Work Order 단위 개별 증거 검증은 위 웨이브와 별개로, 어울1이 특정 `COMPLETED` 보고에 대해 명시적으로 요청할 때만 수행한다. 상시로는 하지 않는다.

### 1.5 설계·계획 Checkpoint와 테스트 웨이브 매핑

작업계획서 §8.1과 설계서 §23.1이 정의한 Release 1 내부 체크포인트(CP1~CP5·RC)는 제품 Go/No-Go 게이트이고, 테스트 웨이브(TP)는 테스트 담당의 독립 실행 단위다. 의미가 다르므로 병행하며 아래로 연결한다. CP 통과 판정에는 대응 TP의 결과가 근거로 들어간다.

| Checkpoint | 작업계획 선행 | 성격 | 대응 테스트 웨이브 | 결정·승인 기록 |
| --- | --- | --- | --- | --- |
| CP1 승인 기준선 | M0·M1 | 문서·환경·독립 Build 기준선 | TP-0 + 어울1 M1 Exit 검증, TP-5에서 독립성 재감사 | 신산님 CP1 Go/No-Go |
| CP2 Production-bound UX | M2·M3 | M3가 승계하는 전체 UX Shell | TP-1 | 신산님 G2-UX |
| CP3 초기 Web Thin Vertical E2E | R1-M6-10 | 단일 PDF·단일 승인 모델 수직 통합 | **TP-2A** | 신산님 CP3 Go/No-Go |
| CP4 지식·모델·Client Beta | M7 | 전체 Source·모델·Connector·Client | TP-3 | 신산님 CP4 Go/No-Go |
| CP5 Studio Beta | M8 | 생성 설정 포함 5종 산출물 수명주기 | TP-4 | 신산님 CP5 Go/No-Go |
| RC 운영 검증 | M9 | 배포·Update·Alarm·Recovery·전체 회귀 | TP-5 | 신산님 G9-RELEASE |

## 2. 검증 대상과 기준 문서

### 2.1 기준 문서 우선순위

1. 신산님의 최신 명시 결정
2. `AGENTS.md` 상시 규칙
3. 승인된 상세 설계서 Markdown 정본 (Hash를 Baseline Manifest와 대조)
4. 승인된 Release 1 작업계획서
5. 개별 Work Order와 작업보고서

TP-0은 Baseline Manifest 생성 전 문서 검증이므로 예외로 한다. TP-0에서는 후보 상세 설계·작업계획·테스트 계획의 경로·버전·직접 계산한 Hash를 보고서에 기록한다. M0 이후의 모든 검증은 기준 문서의 버전·Hash를 `release_1_baseline_manifest.json`과 대조하며 불일치하면 검증을 중단하고 `BLOCKED`로 보고한다.

### 2.2 검증 대상

- 모든 M1~M9 개발 Work Order의 산출물과 완료 증거
- Web(Next.js)·Windows(Tauri+Local Service)·Android(APK)·iOS(Archive/설치 Build) 실행 결과물
- 공개 API(OpenAPI v1)·BFF·Loopback Local API 계약
- Cloud(PostgreSQL+pgvector·Object Storage)·Local(암호화 SQLite·File Store·Vector Index) 데이터 계층
- Model Registry·Routing·Fallback·Managed Local Model·Connector(Daon·인터넷)
- 다섯 Studio 산출물과 검토·승인·전달·생산 지식 등록 수명주기
- 운영·알림·복구·Backup·Restore 화면과 절차

### 2.3 검증 제외

- Release 2·3 기능 (슬라이드, 멀티미디어, Agent 실행 등)
- Daon 내부 시스템 자체의 품질 (Connector 계약 준수만 검증)
- 신산님 승인으로 범위 제외가 확정된 항목 (M0 결정 기록 기준)

## 3. 검증 원칙

1. **증거 없는 통과 없음**: `Done` 표시, 정적 검사 통과, Build 성공, HTTP 200만으로 합격 판정하지 않는다. 실제 화면·파일·Network·프로세스 증거를 요구한다.
2. **재현 가능성**: 모든 테스트는 실행 명령·환경·Build/Commit과 함께 기록하여 제3자가 재현할 수 있게 한다.
3. **독립 수행**: 어울2가 제출한 증거를 신뢰 입력이 아닌 검증 대상으로 취급한다. 핵심 여정은 어울2의 증거와 별개로 직접 재실행한다.
4. **부정 경로 우선**: 성공 경로만이 아니라 실패·차단·만료·권한 없음·장애 경로를 반드시 포함한다. 설계서의 안전 원칙 대부분은 부정 경로에서만 검증된다.
5. **파괴적 검증 금지**: 운영 데이터 대상 Restore·파괴적 장애 주입은 G9-DRILL 승인 없이 수행하지 않는다. 테스트는 전용 Fixture·전용 Tenant/Workspace만 사용하고 Child-first로 정리한다.
6. **비밀 보호**: 증거 수집 시 비밀값·개인정보·민감 원문은 Masking한다.

## 4. 검증 수준

작업계획 §21.1의 4단계 증거 수준을 검증 수준으로 사용한다.

| 수준 | 내용 | 테스트 담당의 확인 방법 |
| --- | --- | --- |
| L1 정적 확인 | Lint, Type, Schema, 금지 패턴, Diff | 결과 로그 재확인, 금지 패턴(Daon 직접 의존·내부 URL·Secret) 독립 재검사 |
| L2 Build | App·Service·Installer·Archive 실제 Build | Build 재현 또는 Artifact Hash·서명 확인 |
| L3 자동 테스트 | Unit, Contract, Integration, Migration, Security, E2E | 테스트 재실행, 커버리지·Fixture 정리 확인 |
| L4 실제 화면·운영 검증 | Browser·EXE·Device 클릭, Network, Process, 장애·복구 | 직접 재실행 또는 시각·Trace ID·Audit 연결이 검증된 증거 대조 |

L1~L3이 전부 통과해도 해당 Work Order가 요구하는 L4 증거가 없으면 불합격이다.

## 5. 설계 적합성 검증

### 5.1 방법

M0에서 어울1이 작성하는 추적표(`release_1_traceability.md`)를 기준으로, 각 설계 조항 ↔ Work Order ↔ 테스트 케이스를 3방향으로 대조한다.

1. **정방향**: 설계 조항마다 담당 Work Order와 본 계획의 테스트 케이스가 있는지 확인한다. 누락 = 커버리지 결함.
2. **역방향**: 구현된 기능마다 근거 설계 조항이 있는지 확인한다. 근거 없는 기능 = 무단 범위 확장(C2 위반 후보).
3. **의미 대조**: 조항의 문구가 아니라 의미가 구현됐는지 확인한다. 예: "사용자 가중치는 권위 등급을 뒤집지 못한다"는 UI 문구가 아니라 실제 검색 결과 순위로 검증한다.

### 5.2 설계 적합성 핵심 대조 항목

| 설계 조항 | 검증 방법 |
| --- | --- |
| §3 불변 원칙 15항 | 본 계획 §8 불변 조건 회귀 |
| §5.3 화면 폭별 동작·상태 보존 | 4개 폭 구간 전환 시 5개 상태 항목 보존 확인 |
| §7.2 권위 순서 | 권위 역전 시도 테스트 (본 계획 §9.1) |
| §7.3 가중치 수식·Clamp | 동일 tier 내 가중치 반영, tier 간 불변 확인 |
| §10.4 Hard Filter → Readiness Filter → 결정론 정렬 | RoutingDecision 원장과 실제 Network 대조 |
| §10.5 Fallback 종료 상태 | 종료 상태 결정표 전수 테스트 (본 계획 §9.2) |
| §16.1 RunSnapshot 불변 | 실행 중·후 Snapshot 변경 시도, ModelAttempt append-only 확인 |
| §17.2 API 공통 원칙 | Idempotency·Optimistic Concurrency·Pagination Contract 테스트 |
| §18.4 안전 오류 | 오류 응답에 Stack Trace·내부 Host·Secret 이름 부재 확인 |
| §20 보안 | 본 계획 §10 보안 테스트 |

## 6. Milestone별 검증 항목과 실행 웨이브

이 절은 각 Milestone에서 **검증 가능해지는 항목**을 정의하되, 테스트 담당이 이를 언제 실행하는지는 §1.4의 웨이브를 따른다. 즉 아래 항목은 Milestone Exit 시점에 어울1이 1차 검증하고, 테스트 담당의 독립 실행은 각 제목의 `→ 실행 웨이브` 표시 지점에서 이루어진다. 기본 진입 조건은 해당 Milestone의 모든 Work Order가 `COMPLETED` 보고되고 어울1이 `VERIFYING`으로 전환한 상태다. 단, TP-2A는 M6 중간 Checkpoint 예외로서 `R1-M6-10`과 그 `depends_on`만 완료·검증되면 실행하며 M6 전체 완료를 요구하지 않는다.

### M1 — 독립 저장소 · → 실행 웨이브 TP-2 (TP-5 재감사)

- 새 환경에서 Lockfile 기준 재현 Build (Web·Windows·Mobile·API·Local Service)
- 독립성 검사 독립 재실행: Daon 내부 DB·URL·패키지·Runtime Image·소스 Import·파일 경로 직접 의존 0건
- CI 실패 시 Merge 차단 동작 확인 (의도적 실패 커밋으로 검증)
- 순환 의존 0건 Dependency Graph 확인

### M2 — 전체 UX·운영 흐름 · → 실행 웨이브 TP-1 (G2-UX)

- 전 화면·상태가 클릭 가능한 한 흐름으로 연결되는지 R1 여정 8종 기준으로 추적
- 미구현 기능이 성공으로 위장하지 않고 `unavailable`/명시 Mock으로 표시되는지 확인
- 4개 반응형 구간 전환과 상태 보존, 키보드 접근성
- 오류·권한·축소 운영·복구 화면의 존재 (정상 경로만 있는 프로토타입은 불합격)
- M2 IA·Route·Design Token·상태·접근성 Component·Layout의 M3 재사용 계약과 Mock Adapter 격리 확인
- Studio Tile 선택 후 목적·독자·Source·RuleSet·분량·출력 형식·검토 조건을 확인하고, 강제 RuleSet·조직 검토 조건이 잠기는지 확인
- G2-UX 기술 검토 의견서 제출 (승인 판단은 신산님)

### M3 — 실행형 Client Shell · → 실행 웨이브 TP-3

- Web: Production Process 실행, Chrome 실제 클릭, 종료·재기동
- Windows: 설치 EXE → 실행 → 종료 후 잔존 Process·Port 0건, Loopback 외 외부 Interface Listen 0건 (`netstat` 증거)
- Android/iOS: 실기기(또는 iOS Simulator) 설치·클릭·Background·재기동
- 클라이언트 바이너리·소스에 내부 API·Provider URL·Secret 문자열 검사 0건

### M4 — 공개 API·인증·권한 · → 실행 웨이브 TP-2 (보안 전 스위트는 TP-5)

- OpenAPI Schema와 실제 응답 Contract 대조 (BFF와 Native Gateway 의미 일치 포함)
- 인증: 로그인·갱신·철회·만료·401, PKCE 흐름, Device 등록
- 권한: 역할별 접근 Matrix, 403/404 정보 비노출, Tenant 교차 접근 0건
- 모든 Write에 Idempotency Key 중복 제출·Optimistic Concurrency 충돌 테스트
- Audit: 각 행위의 Actor·Trace·변경 전후 기록, append-only 변조 시도 거부
- Graceful Shutdown 중 진행 중 요청 처리

### M5 — Local·Cloud Data와 Sync · → 실행 웨이브 TP-2 (Backup·Restore 정식은 TP-5)

- Migration 재적용(멱등)·Rollback, RLS 활성 상태에서 서비스 계정 우회 시도 차단
- Local: 저장 파일이 실제 암호화되어 있는지 raw 파일 검사, OS Secure Store Key 철회 후 접근 불가
- Sync: 무승인 전송 0건 (Network 캡처), 충돌 시 자동 덮어쓰기 0건, Offline Queue → Reconnect
- 삭제·보존: 유예 삭제, 파생 데이터(Index·Preview·Cache·Local Copy) 추적 정리, Legal Hold 우선
- Backup → 격리 Restore → 권한·계보 재검증 (전용 Fixture만)

### M6 — Source·지식·LLM·Connector · → 실행 웨이브 TP-2A(CP3)·TP-2

- 파일 형식 Matrix: 문서·표 7개 형식(PDF·DOCX·PPTX·XLSX·CSV·TXT·Markdown), M0에서 확정한 주요 이미지 각 형식, 음성 3개 형식(M4A·WAV·MP3)의 정상·손상·위장 MIME·압축 폭탄·암호화 파일 처리
- 모든 문서·표·이미지의 Vision/LLM-first 문맥·의미 이해와 의미 청킹, Parser·OCR·Document Parse의 검증·보완 전용 역할 확인
- 정책 Hard Filter 후보 0은 ProcessingRun `policy_blocked`·Source `needs_review`, 정책 허용 후보 Runtime 실패 소진은 ProcessingRun `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`·Source `waiting_model`로 분리하고 Parser-only `ready` 0건 확인
- `partial_understanding`은 일부 범위 Vision/LLM 성공에서만 발생하고 기본 검색·생성 제외, 누락 범위 표시와 재처리·검토·비활성화 전이 확인
- 원문 Evidence: PDF Page·XLSX Cell·이미지 Region·음성 Time Segment 인용 위치 재현
- ASR: Local ASR 실행, TranscriptVersion·Model Digest·시간 구간 계보
- Retrieval: 권위·가중치·Clamp·충돌 (본 계획 §9.1)
- Routing·Fallback: 종료 상태 결정표 (본 계획 §9.2), UI 선택 = Route = Network = Audit 일치
- Managed Local Model: 다운로드·서명·Digest 검증, 설치·Update·Rollback·삭제 전체 수명주기, 사용자 CLI 0건
- Connector: Daon 연결·미연결·장애·재연결, RuleSet Snapshot·만료·차단 (본 계획 §9.3), 인터넷 SSRF·Redirect 방어
- Run Pipeline: accepted→planning→retrieving→generating→validating→completed 상태·Event·취소·재시도·waiting_user
- R1-M6-10 CP3에서 실제 Web 로그인→Workspace→단일 PDF→Vision/LLM 이해→Parser/OCR 검증→색인→질문→인용 원문 열기 조기 E2E 통과

### M7 — 수직 흐름 · → 실행 웨이브 TP-3

- R1-WEB-01, R1-WIN-01(일부), R1-WIN-02, R1-AND-01(일부), R1-IOS-01(일부)을 실제 파일·실제 Client로 직접 재실행
- Windows Local-private: 네트워크 차단 상태에서 전체 로컬 흐름 + 외부 연결 시도 0건 (패킷 캡처)
- 오류·만료·축소 운영 회귀: Source 만료, Index/Daon/LLM 장애, Evidence Store 차단

### M8 — Studio · → 실행 웨이브 TP-4

- Tile 선택 후 즉시 생성되지 않고 목적·독자·Source·RuleSet·분량·출력 형식·검토 조건을 확인하며 GenerationSettingsSnapshot이 Run·OutputVersion에 연결되는지 검증
- 제출 전 설정 변경은 재확정·Output Revision 0건, 제출 후 변경은 새 GenerationRequest·Revision·OutputVersion과 변경 사유로 남는지 검증
- 다섯 산출물 각각: 생성 → 실제 파일 Open(DOCX·PDF·XLSX·CSV·JSON·SVG·PNG) → 내용·Layout·근거 위치 검증
- Version: 사용자 편집과 AI 재생성이 별도 Revision, OutputVersion 불변·previous_version_id, 승인 후 내용·근거·가중치·모델·RuleSet·생성 설정 변경 시 새 Version + 재승인 강제
- 검토·승인·전달: 역할 권한, revision_requested→draft 순환, ApprovalRequest 기본 7일·조직 1~30일·24시간 전 알림·만료·회수 자동 승인 0건, Audit 연결
- 생산 지식: 명시 등록만 가능, 불변 Version, 순환 파생 탐지, Daon 자동 승격 경로 부재
- 내보내기 파일에 산출물 버전·생성 시각·지식 범위·근거 부록 포함

### M9 — 운영·Release 검증 · → 실행 웨이브 TP-5

- 운영자 여정 R1-OPS-01: 정책 설정 → 장애 주입 → 경고 → 재처리 → 복구 (격리 환경)
- 각 플랫폼 서명 Package 설치·Update·Rollback
- 보안 검증 (본 계획 §10 전체)
- 성능·용량·비용: M0에서 확정된 SLO·한도 대비 측정
- 접근성·반응형·지원 OS/Browser Matrix
- G9-INDEPENDENT: 최종 Diff·Evidence Pack 전체 독립 검증 보고서 제출

## 7. R1 필수 여정 E2E 검증

8개 여정은 어울2 증거 대조가 아니라 **테스트 담당이 직접 재실행**한다. 실행 시점은 여정이 성립하는 웨이브를 따른다: 질문·근거 계열(R1-WEB-01, R1-WIN-01·02, R1-AND/IOS 일부)은 **TP-3**, Studio 계열(R1-WEB-02, R1-WIN-03, R1-AND/IOS 잔여)은 **TP-4**, 운영자 여정(R1-OPS-01)과 8개 전체 통합 회귀는 **TP-5**. 각 여정의 단계·합격 증거는 설계서 §23.1을 기준으로 하며, 아래 추가 확인 항목을 더한다.

| 여정 | 직접 재실행 시 추가 확인 |
| --- | --- |
| R1-WEB-01 | RunSnapshot의 지식 범위·가중치가 UI 설정과 일치, 인용 클릭 시 원문 위치 도달 |
| R1-WEB-02 | 5종 파일을 실제 Office/뷰어로 열어 내용 확인, 승인 후 편집 → 재승인 강제 확인 |
| R1-WIN-01 | OS 방화벽/패킷 캡처로 외부 연결 0건 입증, 재연결 후 승인 항목만 Sync |
| R1-WIN-02 | 각 Provider 유형 선택 시 Network 목적지와 EgressDecision 일치 |
| R1-WIN-03 | 설치 App(개발 모드 아님)에서 수행 |
| R1-AND-01 | 권한 거부 시나리오(카메라·마이크 거부 후 재요청), 강제 종료 후 재기동 상태 복원 |
| R1-IOS-01 | Android와 동일 + 플랫폼 알림 권한 흐름 |
| R1-OPS-01 | 잠금 정책이 일반 사용자 화면에 실제 반영되는지 교차 확인 |

## 8. 불변 조건 회귀 체크리스트

작업계획 §6의 15개 불변 조건을 상시 회귀 항목으로 고정한다. **매 Milestone 검증과 G9-INDEPENDENT에서 전체 재확인**한다.

| # | 불변 조건 | 검증 방법 |
| --- | --- | --- |
| 1 | Daon 없이 독립 핵심 흐름 동작 | Daon Connector 비활성 상태에서 §1.2 독립 실행 조건 8항 실행 |
| 2 | Daon 직접 의존 0건 | Dependency Graph + 금지 패턴 검사 독립 재실행 |
| 3 | 강제 RuleSet 해제 불가·Snapshot 없으면 대상 Run만 차단 | 해제 시도(UI·API 직접 호출 모두) + Snapshot 제거 후 Run 상태 확인 |
| 4 | Daon 승인 지식 권위 우선, 가중치로 역전 불가 | §9.1 권위 역전 시도 |
| 5 | 충돌·근거 부족 미은폐 | 의도적 충돌 Fixture로 표시·기록 확인 |
| 6 | LLM 일반지식 인용 위장 금지 | 문서 근거 없는 답변에서 `LLM 자체 지식` 표시 확인 |
| 7 | 생산 지식 명시 등록·불변 버전 | 자동 등록 경로 부재, 등록 버전 수정 시도 거부 |
| 8 | Local-private 무승인 이동 금지 | 패킷 캡처 + EgressDecision 부재 확인 |
| 9 | 모델 직접 선택의 정책 우회 불가 | 금지된 Deployment를 API로 직접 지정 → `policy_blocked` |
| 10 | 무승인 Provider 자동 Fallback 금지 | 허용 후보 전체 장애 유도 → 승인 외 모델 호출 0건 |
| 11 | 모든 Run에 불변 Snapshot | Snapshot 필드 완전성 + 사후 변경 시도 거부 |
| 12 | 클라이언트에 내부 URL·Secret 없음 | 바이너리·저장소·Network 문자열 검사 |
| 13 | 화면·공개 API로만 운영 (Python·DB·CLI 불요) | 각 운영 절차를 화면만으로 완주 |
| 14 | 모든 문서·표·이미지의 Vision/LLM-first 의미 이해 | ProcessingRun의 의미 이해 모델·Digest·Prompt·Policy와 의미 청킹 결과 확인 |
| 15 | Parser·OCR·Document Parse는 검증·보완 전용, Parser-only 완료 금지 | 이해 모델 장애·차단 Fixture에서 `ready` 0건, 보조 추출·불일치·검토 계보 확인 |

## 9. 핵심 결정표 테스트

### 9.1 권위·가중치·충돌

| TC | 입력 | 기대 결과 |
| --- | --- | --- |
| AUTH-01 | 하위 tier Source에 최대 가중치, 상위 tier와 내용 충돌 | 상위 tier가 최종 기준, 하위는 `충돌·대안` 표시 |
| AUTH-02 | Daon 승인 지식 활성 + 관련 질의 | 최소 포함 슬롯 보장 확인 (RunSnapshot 대조) |
| AUTH-03 | 같은 tier 내 가중치 차등 | within_tier_score 순위 반영 확인 |
| AUTH-04 | 관리자 잠금 범위 밖 가중치 설정 시도 | Clamp 적용 + Snapshot에 Clamp 기록 |
| AUTH-05 | Source 제외 목적으로 가중치 0 설정 | 비권장 — 활성화 설정으로 안내되는지 확인 |
| AUTH-06 | 해결되지 않은 중요 충돌 | 검토 상태 전환 확인 |

### 9.2 Routing·Fallback 종료 상태 (설계서 §10.5)

| TC | 상황 | 기대 종료 상태 |
| --- | --- | --- |
| RT-01 | Hard Filter 후 허용 후보 0 | `policy_blocked` + 원인 Code |
| RT-02 | `auto`, 정책 후보 있음, Runtime Ready 0 | 재시도 가능 `failed` + `NO_AVAILABLE_DEPLOYMENT` |
| RT-03 | `auto`, Timeout/Rate Limit/일시 장애 | 같은 PolicyVersion 안 다음 후보 자동 시도, ModelAttempt 기록 |
| RT-04 | `pinned`, 정책상 금지 모델 | `policy_blocked` |
| RT-05 | `pinned`, 허용 모델의 Offline/Capacity 문제 | `waiting_user` (무단 모델 변경 0건) |
| RT-06 | 인증 오류·잘못된 요청 | 재시도 불가 `failed`, 다른 Provider 우회 0건 |
| RT-07 | Local-private에서 External 후보 | 후보 제외 (자동 Fallback 0건) |
| RT-08 | Stream 일부 출력 후 모델 장애 | 다른 모델 이어쓰기 0건 |
| RT-09 | `local_only(device_only)` vs `(private_org_allowed)` | 후보 집합 차이 정확성 |
| RT-10 | Embedding 모델·버전·차원 변경 | 새 IndexVersion 생성 |

### 9.3 RuleSet 상태 (설계서 §7.2, §8.4)

| TC | 상황 | 기대 결과 |
| --- | --- | --- |
| RS-01 | 강제 Binding + 유효 Snapshot | 잠금 상태 적용, 사용자 해제 불가 |
| RS-02 | 강제 Binding + Connector 중단 + 허용 기간 내 Snapshot | 계속 적용 |
| RS-03 | 강제 Binding + 유효 Snapshot 없음 | 적용 대상 Run만 `policy_blocked/RULESET_UNAVAILABLE`, Source 등록·조회·기승인 결과 열람은 유지 |
| RS-04 | Binding 없는 Workspace + Daon 전체 장애 | 독립 기능 전부 동작 |
| RS-05 | 선택형 `warn_and_skip` + Snapshot 없음 | 화면·RunSnapshot에 누락 공개 후 계속 |
| RS-06 | 선택형 `block` + Snapshot 없음 | `policy_blocked` 종료 |
| RS-07 | 선택형 Binding 변경 권한 | Workspace 관리자만, 강제는 조직 관리자만, ETag·Audit 기록 |

### 9.4 상태 전이 (설계서 §18)

- Source: `registered→security_check→processing→indexing→ready`와 분기 8종 — 각 분기 진입·이탈 경로, 미정의 전이 거부
- Run: `accepted→planning→retrieving→generating→validating→completed` 정상 6단계와 분기 5종 — 취소는 어느 단계에서든, `completed` 후 상태 변경 거부
- OutputVersion: 수명주기 전이와 `approved` 후 편집 → 새 Version 강제
- ApprovalRequest: `pending→approved|rejected|expired|withdrawn`, `rejected` 시 대상 OutputVersion의 `revision_requested` 전환

## 10. 보안·개인정보 테스트

| 영역 | 항목 |
| --- | --- |
| Tenant 격리 | 교차 Tenant 접근 시도(API 직접 호출 포함) 0건 허용, RLS + Service Authorization 이중 확인 |
| 인증 | 만료 Token, 위조 Token, 철회된 Session·Device, Loopback 단기 Token 위조 |
| 권한 상승 | 조회자→편집, 편집자→승인, 일반→조직 관리자 각 시도 차단 |
| Secret | 클라이언트·로그·오류 응답·증거 파일 내 Secret·내부 URL 문자열 0건 |
| Egress | 모든 외부 Provider 호출에 선행 EgressDecision 존재, Masking 정책 적용 |
| SSRF | 인터넷 Connector에 내부망 IP·Redirect 체인·DNS Rebinding 시도 |
| Prompt Injection | 명령이 삽입된 문서 Source로 질의 → 도구 호출·정책 우회 0건, 데이터로만 취급 |
| Tool 권한 | LLM Tool Call의 권한·Scope·비용·Timeout 재검사, Read와 Write/Approval 도구 분리 |
| Local API | 외부 Interface 접근 거부, 위조 App Instance, Allowlist 외 Command 거부 |
| 감사 | Audit append-only, 변조·삭제 시도 거부, 위변조 방지 확인 |

## 11. 결함 보고와 분류

### 11.1 결함 보고 형식

```text
defect_id: DEF-R1-<번호>
발견 시점: <Milestone/Gate/여정>
심각도: <아래 분류>
유형: 기능 결함 | 설계 위반 | 불변 조건 위반 | 보안 결함 | 증거 불충분 | 문서 불일치
관련 설계 조항 / Work Order / TC:
재현 절차 (환경·Build/Commit 포함):
기대 결과 vs 실제 결과:
증거 경로:
```

### 11.2 심각도

| 심각도 | 기준 | 처리 |
| --- | --- | --- |
| S1 Critical | 불변 조건 위반, 보안 결함, 데이터 손실·유출, 무단 외부 전송 | 즉시 어울1 보고, 해당 범위 진행 중단 권고 |
| S2 Major | 필수 여정 차단, 설계 조항 위반, 완료 증거 허위·불충분 | 해당 Work Order 불합격, 재작업 대상 |
| S3 Minor | 표시·문구·비차단 UX 문제, 경미한 증거 미비 | 수정 권고, Gate 통과 가능 여부는 어울1 판정 |
| S4 Observation | 개선 제안, 잠재 위험 | 기록만, 어울1 참고 |

### 11.3 처리 흐름

```text
테스트 담당 결함 보고
→ 어울1 판정 (유효성·분류·변경 등급 C0~C3)
→ C0/C1: 어울1이 재작업 지시 → 수정 후 테스트 담당 재검증
→ C2/C3: 신산님 승인 요청
→ 재검증 통과 시 defect_id 종결 (증거 연결)
```

설계 위반 결함에서 "구현이 맞고 설계가 틀린" 경우도 어울1에게 회부한다. 테스트 담당이 임의로 설계 해석을 바꾸지 않는다.

## 12. 산출물과 저장 경로

| 산출물 | 경로 |
| --- | --- |
| 본 테스트 계획 | `docs/04_test_reports/release_1_test_plan.md` |
| 테스트 웨이브 보고서 | `docs/04_test_reports/release_1/wave_<TP-ID>.md` |
| Checkpoint·Gate 결정 기록 | `docs/04_test_reports/release_1/approval_<Checkpoint-or-Gate>.md` |
| Milestone 검증 보고서 | `docs/04_test_reports/release_1/verification_M<번호>.md` |
| 여정 E2E 검증 보고서 | `docs/04_test_reports/release_1/journey_<여정ID>.md` |
| 결함 대장 | `docs/04_test_reports/release_1_defect_register.md` |
| G9-INDEPENDENT 독립 검증 보고서 | `docs/04_test_reports/release_1_independent_verification.md` |
| 검증 증거 | `docs/03_evidence/release_1/verification/<defect_id 또는 보고서ID>/` |

모든 보고서는 검증 일시, 대상 Build/Commit, 기준 문서 Hash, 사용 환경을 머리에 기록한다. TP-0·TP-1·TP-2A·TP-2·TP-3·TP-4·TP-5 결과는 해당 웨이브 보고서로 신산님에게 제출하며, Go/No-Go 결정은 별도 승인 기록에 승인자·일자·조건·근거 보고서 Hash와 함께 남긴다.

## 13. 환경·도구 요구사항

M0 결정(R1-D001, D011, D012)에 의존하며, 확정 전 항목은 `미확정`으로 관리한다.

| 항목 | 요구사항 | 상태 |
| --- | --- | --- |
| Web | 지원 브라우저 목록의 실제 Chrome 이상 1종 + DevTools Network/Console | 브라우저 목록 미확정 |
| Windows | 실제 Windows PC, 패킷 캡처 도구, `netstat`/Process 검사 | 가용 |
| Android | 실기기 최소 1대 | 미확정 |
| iOS | macOS Build Host/CI Runner, 고정 Xcode·CocoaPods·RN Toolchain, Apple Team·Signing·Provisioning + Device/Simulator | **미확정 · 위험** — 미확보 시 iOS Work Order와 R1-IOS-01 검증 불가 |
| Daon | 공식 Sandbox 접근 자격 | 미확정 (R1-D007) |
| Local LLM | 진단 기준을 충족하는 GPU/CPU 장비 | 미확정 (R1-D006) |
| 장애 주입 | 네트워크 차단, Provider Mock 장애, Queue 적체 유도 수단 | M6 전 준비 |
| 성능 측정 | M0 SLO 기준 부하 도구 | SLO 미확정 (R1-D010) |

## 14. 위험과 전제

| 위험 | 영향 | 대응 |
| --- | --- | --- |
| iOS macOS Build Host/Runner·고정 Xcode·Apple Signing 환경 미확보 | iOS Work Order와 R1-IOS-01 전체 검증 불가 | M0에서 Host·Toolchain·Team·Provisioning·Device 확보 여부 확정, 미확보 시 `BLOCKED`; 범위 제외는 신산님 C2 승인 필요 |
| Daon Sandbox 지연 | RS-01~04, R1-WIN-02 검증 지연 | Connector 계약 Mock으로 선행 검증하되 최종 판정은 공식 Sandbox로만 |
| M2 프로토타입과 M3 실물 괴리 | G2-UX 승인 화면과 실물 불일치 | M3 검증 시 G2-UX 승인 기준 Version과 화면 대조 항목 포함 |
| SLO·한도 미확정 상태 지속 | 성능 검증 기준 부재 | M0 Exit에서 R1-D010 확정 확인, 미확정 시 M9 진입 `BLOCKED` 보고 |
| 어울2 증거의 자기 보고 편향 | 허위·불충분 완료 | 핵심 여정 직접 재실행 원칙(§7) 유지 |

## 15. 승인

| 확인 항목 | 승인자 | 상태 |
| --- | --- | --- |
| 본 테스트 계획의 범위·기준 | 신산님 | 승인 · 2026-07-20 · `APR-TP0-20260720-01` |
| 검증 절차의 기술 타당성 | 어울1 | 승인 · 2026-07-20 |

본 계획은 상세 설계서(G0-DESIGN)와 작업계획(G0-PLAN)의 승인본을 기준으로 한다. M0 Baseline Manifest가 확정되면 해당 문서의 확정 Hash를 본 계획의 검증 기준으로 고정한다.

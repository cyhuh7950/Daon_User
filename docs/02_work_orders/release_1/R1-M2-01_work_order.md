# 작업지시서 R1-M2-01 · 제품 IA·Design Token 확정

## 0. 문서 정보

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M2-01` |
| issue_id | `R1-M2-01-I001` |
| Version | `1.0` |
| 작성일 | `2026-07-21` |
| 작성·기술 판단 | 어울1 |
| 실행 | 어울2 · `daon-developer` |
| 기준 Branch | `codex/r1-m2-01` |
| 기준 Commit | `36a0b5b6e0a2f2b1c3125ffa76089be00eb790b0` |
| 선행 Work Order | `R1-M1-05` · 신산님 TP-2 M1 조건부 GO 승인 |

## 1. 승인 정본

다음 문서를 요약본으로 대체하지 말고 EOF까지 읽은 뒤 수행한다.

| 정본 | 경로 | SHA-256 |
| --- | --- | --- |
| 상세 설계서 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| Release 1 테스트 계획 | `docs/04_test_reports/release_1_test_plan.md` | `C45DAE31FD408AF0D8885E006E570CC3BE36852A9F925811F8BC329C85ED9D13` |
| M1 최종 보고 | `docs/02_work_orders/reports/R1-M1-05_attempt-1.md` | Git 기준선의 Blob |

우선 적용 조항은 상세 설계 §4~5, §23~24, §27의 확정 결정과 Release 1 계획 §4.3, §7.4, §12의 `R1-M2-01`이다.

## 2. 단일 목표와 완료 모습

### 단일 목표

홈·Workspace·전달함·이력·알림·설정·운영 상태의 전역 IA와 Production-bound Design Token·접근성 계약을 기계 판독 가능한 단일 기준선으로 구현한다.

### 사용자 관점 완료 모습

- 사용자는 역할과 클라이언트에 맞는 전역 이동 구조를 일관되게 이해할 수 있다.
- 후속 M2 화면은 같은 Route ID, 화면 목록, 색·간격·Typography·상태·반응형 Token을 재사용한다.
- 설명은 상시 Box가 아니라 `i` 아이콘·Tooltip·Popover로 제공되고, 필수 오류·경고·진행은 숨겨지지 않는다.
- 아직 구현되지 않은 기능은 `unavailable` 또는 명시 Mock으로만 표현되며 성공 상태로 위장되지 않는다.
- M3는 IA·Route·Token·접근성 계약을 폐기하지 않고 플랫폼 Adapter로 승계할 수 있다.

## 3. 포함 범위

### 3.1 전역 IA와 Route 계약

다음 전역 영역을 빠짐없이 포함한다.

1. 홈
2. 워크스페이스 목록과 개별 워크스페이스
3. 전달함
4. 작업·실행 이력
5. 알림
6. 모델·연결 설정
7. 계정 설정과 조직 설정
8. 운영 상태

각 Route는 안정적인 `route_id`, Web Pattern, Native Route Key, Navigation Group, 허용 Client, 허용 Role, Breadcrumb, 화면 제목 Key, Required Capability, 기본·Loading·Empty·Unavailable·Forbidden·Error 상태를 가진다. URL에 Tenant Secret·내부 서비스 주소·Provider Raw Code를 넣지 않는다.

### 3.2 화면 목록

화면 목록은 최소 다음 필드를 가진 기계 판독 JSON 정본으로 작성한다.

`screen_id | route_id | purpose | clients | roles | entry_points | primary_actions | states | help_interface | evidence_links | production_bound_owner | mock_boundary`

- Client는 `web | windows | android | ios`만 허용한다.
- Role은 설계 §4.1의 7종만 허용한다.
- 상태는 최소 `loading | empty | ready | warning | error | forbidden | unavailable`을 포함한다.
- Mock은 Adapter 경계와 교체 대상이 명시되지 않으면 금지한다.

### 3.3 Design Token 정본

플랫폼 중립 JSON을 정본으로 하고 Web CSS Variable·TypeScript Export는 정본에서 파생하거나 값 일치를 검증한다.

- Typography: 본문·Form `12px`, 작은 설명 `10px`, 아주 작은 보조 `9px`, Sidebar 제목 `14px`, 화면 제목 `16px`.
- Breakpoint: `1440+`, `1024~1439`, `600~1023`, `599-`.
- Spacing: 4px 기반 `0, 4, 8, 12, 16, 24, 32, 40, 48`.
- Radius: `4, 8, 12, 999`.
- 기본 Palette:
  - Canvas `#F4F7FB`, Surface `#FFFFFF`, Muted Surface `#EAF0F6`
  - Primary Text `#172033`, Secondary Text `#4B5B73`, Border `#C7D2E0`
  - Accent `#2563EB`, Accent Hover `#1D4ED8`, Focus `#0369A1`
  - Success `#0F766E`, Warning `#B45309`, Danger `#B91C1C`
  - Daon 승인 지식·RuleSet Authority `#6D28D9`
- Status는 색만으로 구분하지 않고 Label·Icon·텍스트를 함께 요구한다.
- Motion은 `120ms | 180ms | 240ms` 세 단계만 허용하고 Reduced Motion에서 제거·축소한다.
- 최소 Target은 WCAG 2.2 AA의 `24×24px`, Desktop 기본 Control은 `32px`, Touch 우선 Control은 `44px`로 한다.
- Dark Theme 구현은 이번 범위에서 제외하되 Semantic Token 이름은 색상 자체가 아니라 역할을 나타낸다.

### 3.4 접근성 기준

- 목표: WCAG 2.2 Level AA.
- Keyboard만으로 전역 Navigation·Menu·Tooltip·Popover·Dialog를 열고 닫고 이동할 수 있어야 한다.
- Focus Indicator는 가려지지 않고 Semantic Focus Token을 사용한다.
- Text Contrast는 일반 Text 4.5:1 이상, 큰 Text와 UI 경계는 3:1 이상을 기계 검증한다.
- Icon-only Control은 Accessible Name과 Tooltip/Popover를 함께 가진다.
- Tooltip은 Hover에만 의존하지 않고 Focus·Touch 접근을 지원한다.
- 오류·경고·진행·권한 차단은 Tooltip에만 숨기지 않는다.
- OS 글꼴 확대와 Screen Reader Label 계약을 화면 목록에 연결한다.

### 3.5 M1 경미 위험 흡수

GitHub 공식 Node.js 24 Runtime 지원 범위로 다음만 변경한다.

- `actions/checkout@v4 → actions/checkout@v5`
- `actions/setup-node@v4 → actions/setup-node@v5`
- `actions/upload-artifact@v4 → actions/upload-artifact@v6`

`setup-node`의 명시적 npm Cache·Node Pin 동작, 최소 권한, Step ID, stale Evidence 제거, Fallback, Artifact 이름과 Gate 계약을 보존한다. 다른 Action·Toolchain·Lockfile은 변경하지 않는다. GitHub Hosted Runner에서 Node.js 20 Deprecated Annotation이 사라졌는지 실제 PR Run으로 확인한다.

## 4. 권장 산출물 경계

실제 저장소 구조를 확인해 같은 책임을 더 명확히 배치할 수 있으면 증거와 함께 어울1에게 보고한다. 승인 없이 새 Workspace Package를 추가하지 않는다.

- `packages/contracts/`: 전역 IA·Route·화면 목록의 플랫폼 중립 계약
- `packages/design-tokens/`: Token JSON 정본, CSS Variable, TypeScript Export
- `packages/ui/`: 접근성·상태·설명 인터페이스의 플랫폼 독립 계약. DOM·React Native 강제 공유 금지
- `docs/01_architecture/`: 사람이 읽는 Sitemap·화면 목록·Token·접근성 설명
- `scripts/tests/`: IA·Token·접근성·Workflow 계약 Test
- `.github/workflows/release-1-quality-gate.yml`: §3.5의 세 Action Major만 변경

필요할 때만 기존 `package.json`, 해당 Workspace Manifest, `quality-gate-policy.json`, 검증 Script를 최소 변경한다. 새 외부 Runtime Dependency 추가는 금지한다.

## 5. 제외 범위

- R1-M2-02의 3면 Workspace Layout·반응형 전환 구현
- 실제 Next.js Page·BFF·API·DB·인증·LLM·검색·파일 처리
- Windows·Android·iOS 실행 Shell
- Dark Theme, Brand Logo, Marketing 화면
- 실제 기능이 있는 것처럼 보이는 임시 Mock Service
- 기존 Daon2·Daon2.5·Daon3 Source 또는 Module 재사용
- 기존 `shared-db`, `common`, `netdata`, `proxy` 변경
- 승인 없는 공개 API·데이터 계약·보안 경계 변경

## 6. 기존 기능과 불변조건

- 독립 저장소·의존성 경계와 7범주 품질 Gate를 유지한다.
- Browser Source가 생기더라도 API 절대주소, `localhost`, Docker 내부 주소, `NEXT_PUBLIC_API_BASE_URL` Client Fetch를 넣지 않는다.
- Daon 승인 지식·RuleSet 우선권과 사용자 가중치 계약을 UI 색상만으로 표현하지 않는다.
- Vision/LLM-first와 Parser/OCR 보조 계약을 화면 명칭·설명에서 뒤집지 않는다.
- 1920×1080·12px 기준과 네 반응형 구간을 바꾸지 않는다.
- Production-bound 자산은 M3 승계 Owner와 Adapter 경계를 가진다.

## 7. 실행 단계

| 단계 | 작업 | 완료조건 |
| --- | --- | --- |
| S0 | 정본·Hash·기준 Commit·현재 Diff 확인, 진행 기록 착수 | 승인 정본·범위·선행 변경 확인 |
| S1 | 실패하는 IA·Token·접근성·Action Version Test 작성 | 누락 계약이 Red로 재현됨 |
| S2 | M1 Action Runtime 경고 최소 보완 | 세 Action만 승인 Major로 변경, 기존 Workflow 계약 유지 |
| S3 | 전역 IA·Route·화면 목록 정본 구현 | 8개 영역·Role·Client·상태·Mock 경계 Schema PASS |
| S4 | Token 정본과 Adapter 구현 | JSON·CSS·TS 값 일치, Font·Breakpoint·Palette·Contrast PASS |
| S5 | 접근성·Sitemap·승계 문서화 | WCAG 2.2 AA·설명 인터페이스·M3 Owner 연결 |
| S6 | 로컬 전체 검증 | 신규 Test, 기존 25 Test, Toolchain, 독립성, 7범주 Gate PASS |
| S7 | Diff·허용 범위·보호 파일 검증 후 Hand-off | `HANDOFF_READY`, 어울1 Commit·Push 대기 |
| S8 | 어울1의 불변 SHA 전달 후 ysna-server·GitHub CI 검증 | ARM64·정확 SHA·Gate·Migration N/A·서버 자원 불변·Annotation 0 |
| S9 | Evidence·결과보고 | 완료조건 전수 대조와 정식 상태 제출 |

S7 이후 구현 코드를 수정하지 않는다. 어울1이 Diff를 검토해 Commit·Push한 불변 SHA를 전달한 뒤 같은 어울2가 S8부터 재개한다.

## 8. 테스트와 증거

### 필수 자동 검증

- IA Route ID·URL·Native Key 중복 0
- 8개 전역 영역 누락 0
- Client·Role Allowlist와 상태 7종 누락 0
- 모든 Screen의 Production Owner·Mock Boundary 계약
- Token JSON·CSS·TypeScript Export 값 일치
- Font·Breakpoint·Spacing·Radius·Motion 정확값
- Semantic Color Contrast와 색상 단독 상태 표현 금지 계약
- WCAG 2.2 AA 핵심 접근성 기준
- Workflow 세 Action Major와 기존 Step 순서·Fallback·Artifact 계약
- 기존 품질 Gate Test 25건과 추가 Test 전부 PASS
- `npm run verify:toolchain`
- `npm run verify:independence -- --no-write`
- `npm run verify:quality-gate`
- `git diff --check`, 추적 삭제 0, 허용 범위 밖 변경 0

### 서버·GitHub 증거

- 정확한 Push Commit SHA와 Clean Checkout
- ysna-server ARM64 격리 검증과 기존 자원 사전·사후 불변
- Schema/Migration 경로가 없으면 `NOT_APPLICABLE_NO_SCHEMA`를 근거로 기록하고 DB 명령을 실행하지 않는다.
- PR Required Check 성공과 Branch Protection 유지
- Node.js 20 Deprecated Annotation 고유 건수 `0`
- Artifact의 Merge Ref·부모·PASS/Exit 0·7범주·Failures 0

## 9. 진행 복구 기록

진행 파일은 `docs/04_test_reports/release_1/R1-M2-01_progress.md`다.

어울2는 착수, 각 단계 완료, 오류 발생·복구, Test 완료, Hand-off와 종료 직전에 다음을 기록한다.

`시각 | 단계 | 상태 | 변경 파일 | 명령·Exit | 검사 결과 | 오류·원인 | 복구·대안 | 증거 경로 | 남은 위험 | next_action`

예기치 않은 중단 뒤에는 마지막 성공 단계부터 이어서 수행하며 같은 설치·서버 검증을 근거 없이 반복하지 않는다.

## 10. 결과보고 계약

결과보고 경로는 `docs/02_work_orders/reports/R1-M2-01_attempt-1.md`다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

검토 출력은 `판정 → 판단 이유 → 조치` 순서로 작성한다. 중대 미진은 별도 수정 작업지시서로 회부하고, 합격 가능한 경미 보완은 다음 작업지시서에 흡수한다. 사소한 이유로 합격 작업 전체를 다시 열지 않는다.

## 11. 승인 경계

- 위 IA·Token·접근성·Action Version 계약 안의 구현 방법은 어울1 판단 범위다.
- 기능 범위·요구사항·공개 API·데이터 계약·보안 경계·중요 위험을 바꾸려면 쓰기를 중지하고 신산님 승인을 요청한다.
- Commit·Push·PR·Branch Protection 변경은 어울1이 수행한다.
- 외부 운영 배포·파괴적 작업은 이번 범위가 아니다.

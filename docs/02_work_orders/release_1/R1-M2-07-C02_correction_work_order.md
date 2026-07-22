# R1-M2-07-C02 수정 작업지시서 — 역할 Allowlist·재시도 부모 Fail-close

## 1. 판정과 작업 계약

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M2-07` |
| 동일 issue_id | `R1-M2-07-I001` |
| 현재 분류 | `INCOMPLETE 2/3`, 정식 `FAILURE_REPORT` 0회 |
| 수정 작업 | Recovery 역할 Allowlist와 재시도 부모 부재 Fail-close의 최소 보정 |
| 개발자 | 동일 어울2 · Project Custom Agent `daon-developer` |
| Branch/Worktree | `codex/r1-m2-07` · `C:\tmp\Daon_User-r1-m2-07` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M2-07_progress.md`에 `C02-*` 단계 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-07_correction-2.md` |

원 작업지시서와 C01 수정 작업지시서의 나머지 계약은 그대로 유지한다. 착수 전 현재 Diff와 단일 Writer 상태를 확인하고 Progress에 기록한다.

## 2. 독립 검토 판정

C01은 기존 C2/C3 공격, 전용 20/20, Manifest 31/31, Browser JSON과 상태 PNG 12/12 연결을 해소했다. 다만 아래 두 결함 때문에 `COMPLETED`를 수락하지 않는다.

### C2-1 Recovery Membership 역할 우회

현재 Recovery 권한 판정은 활성 Membership과 Capability만 검사한다. `viewer` Membership에 `recovery.restore.preview` Capability를 주입하고 유효 Step-up·G9를 전달하면 `RECOVERY_PREVIEW_ONLY`, 성공 Audit, Step-up 소비가 발생한다. C01 계약의 Viewer 거부를 위반한다.

### C3-1 재시도 부모 부재 예외

`processingRuns=[]` 또는 완료 Run만 있는 `waiting_model` 상태에서 수동 재시도하면 `previous.id` 접근으로 `TypeError`가 발생한다. 불완전·과거 상태도 정책적 안전 코드로 종료해야 한다.

## 3. 수정 계약

### 3.1 Recovery 역할 Allowlist

- Recovery Preview의 역할 Allowlist를 정본 상수로 명시한다. 승인 역할은 기존 계약과 UI 의미에 맞는 운영 역할만 포함하고 `viewer`는 반드시 제외한다.
- 권한 판정은 `활성 Membership → Tenant/Workspace → 역할 Allowlist → Capability → Step-up → G9` 순서로 현재 정본을 검증한다.
- Action Payload의 Role·Grant는 계속 신뢰하지 않는다.
- `viewer`가 Recovery Capability와 유효 Step-up·G9를 모두 보유해도 `RECOVERY_AUTHORIZATION_DENIED`로 거부한다.
- 거부 시 Preview·성공 Audit·Step-up 소비·외부 효과는 모두 0건이다. 거부 Audit은 허용한다.

### 3.2 재시도 부모 부재 Fail-close

- 새 재처리 Run 생성 전에 동일 SourceVersion·필수 역할의 가장 최근 재시도 가능한 실패 Run이 존재하는지 확인한다.
- 부모가 없으면 `RETRY_PARENT_NOT_FOUND` 안전 코드로 종료하고 예외를 던지지 않는다.
- 자동·수동 두 경로 모두 Event 처리 상태, Idempotency Key, ProcessingRun, Queue, 성공 Audit을 변경하지 않는다. 필요하다면 거부 Audit만 남긴다.
- 기존 정상 재시도 계보와 중복 억제 동작은 유지한다.

## 4. TDD·검증 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| C02-S0 | 현재 Diff·단일 Writer 확인, Progress 기록 | 착수 기록 |
| C02-S1 | 두 공격 재현 Test 선작성 | 유효 RED |
| C02-S2 | 역할 Allowlist·부모 부재 최소 보정 | 전용 Test Green |
| C02-S3 | 전용·전체 회귀·Lint·Build·공통 Gate | 전부 PASS |
| C02-S4 | Browser 상태 회귀·Manifest Hash/Byte 재검증 | 기존 직접 증거 유지 또는 정직한 갱신 |
| C02-S5 | Adapter 계약·Progress·결과보고 정합 | 종료 보고 |

필수 신규 회귀 Test:

1. Viewer + Recovery Capability + 유효 Step-up/G9 → 권한 거부, Preview·성공 Audit·Step-up 소비·외부 효과 0건
2. 실패 부모 Run 0건인 수동 재시도 → 예외 0, `RETRY_PARENT_NOT_FOUND`, Run·Queue·Idempotency 변경 0건
3. 완료 Run만 있는 수동 재시도 → 동일 Fail-close
4. 실패 부모 Run 0건인 적격 Readiness Event → 예외 0, 새 Event/Run/Queue/Processed Event 변경 0건
5. 기존 승인 역할의 유효 Recovery Preview와 정상 실패 부모 재시도는 계속 Green

## 5. 변경·금지 범위와 결과보고

수정은 Operations/Recovery Model·전용 Test·Adapter 계약·R1-M2-07 Evidence/Manifest·Progress·C02 보고로 제한한다. 화면 파일은 실제 표시 계약 변경이 필요한 경우에만 최소 수정한다.

Dependency·Lockfile·Toolchain·CI, 기존 M2-01~06 구현, 실제 API/Queue/DB/Backup/Restore/Update/배포는 변경하거나 실행하지 않는다. 기존 Green을 삭제·완화·Skip하지 않는다. Browser same-origin·Secret 금지와 R1-D022 운영 Release 금지를 유지한다.

각 단계와 오류·복구·테스트를 기존 Progress에 즉시 기록한다. 결과보고는 `판정 → 판단 이유 → 조치` 순서와 `COMPLETED | FAILURE_REPORT | INCOMPLETE` 중 하나를 사용한다. Commit·Push·PR·Merge·외부 배포는 수행하지 않는다.

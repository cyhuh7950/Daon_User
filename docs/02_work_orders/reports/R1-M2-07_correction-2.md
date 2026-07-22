COMPLETED | R1-M2-07-I001 | C02 Recovery 역할 Allowlist와 재시도 부모 부재 Fail-close 보정 | Model·전용 Test·Adapter 계약·Domain/Browser JSON·Manifest·Progress | 신규 공격 5건 포함 전용 25/25·전체 순차 166/166·Lint·Build·Gate·Browser·Manifest PASS | C02 Browser Resource Timing unavailable, 실제 Adapter는 M4~M9 deferred_actual | 어울1 읽기 전용 재검토 후 Commit·Push 판단

# R1-M2-07 Correction 2 결과보고

## 판정

`COMPLETED` — `INCOMPLETE 2/3`의 두 원인인 Recovery 역할 우회와 재시도 부모 부재 예외를 승인 범위 안에서 최소 보정했다. 신규 공격 Test 5건, 전체 회귀, Production Build·공통 Gate와 실제 Browser 정상 경로가 모두 통과했고 미해결 C2/C3는 없다. Commit·Push·PR·Merge·외부 배포는 수행하지 않았다.

## 판단 이유

### Recovery 역할 Allowlist

- Recovery Preview 실행 역할을 현재 Membership 정본의 `organization_admin | operator`로 제한했다.
- 검증 순서는 `활성 Membership → Tenant/Workspace → 역할 Allowlist → Action Capability → StepUpAuthorization → G9 Approval`이다.
- `viewer`가 Recovery Capability와 유효한 Step-up·G9를 모두 보유해도 `RECOVERY_AUTHORIZATION_DENIED`로 닫힌다.
- 거부 경로는 Preview, 성공 Audit, Step-up 소비와 실제 효과를 만들지 않는다. Action Payload의 Role·Grant는 계속 정본으로 신뢰하지 않는다.
- 승인된 `organization_admin`의 정상 Preview 경로와 C01의 Step-up/G9 검증은 그대로 통과했다.

### 재시도 부모 부재 Fail-close

- 자동 Readiness와 수동 Retry는 같은 SourceVersion·필수 역할의 가장 최근 실패 ProcessingRun을 새 Run의 부모로 먼저 찾는다.
- Run이 비어 있거나 완료 Run만 있어 재시도 가능한 실패 부모가 없으면 예외 없이 `RETRY_PARENT_NOT_FOUND`를 반환한다.
- 부모 부재 거부는 Readiness Event, processed Event, Idempotency Key, Run, Queue, 성공 Audit와 Source 상태를 변경하지 않는다.
- 정상 실패 부모가 있는 기존 경로는 `RETRY_PREVIEW_QUEUED`와 `retry_of_processing_run_id: processing-run-failed-001` 계보를 유지한다.

### TDD·자동 검증

| 검증 | 결과 |
| --- | --- |
| C02 최초 RED | 기존 21 PASS / 신규 원인 4 FAIL; Viewer 우회와 부모 부재 TypeError를 직접 재현 |
| 전용 최종 | 25/25 PASS |
| 전체 선택 회귀 | `--test-concurrency=1` 166/166 PASS |
| Workspace Lint | 11 files PASS |
| Web Production Build | PASS; 7 Route |
| 공통 Quality Gate | Exit 0, failures 0; 7범주 모두 PASS |
| Manifest | 31/31 SHA-256·Byte PASS |

공통 Gate가 갱신한 범위 밖 R1-M1-05 결과 2개는 판독 후 HEAD 정본으로 복원했다. Dependency·Lockfile·Toolchain·CI는 변경하지 않았다.

### 실제 Production Browser와 증거 역할

- 새 Production Build의 `/operations`를 Chrome 1920×1080에서 직접 열어 Readiness 클릭 결과 `RETRY_PREVIEW_QUEUED`, 계보 `retry_of_processing_run_id: processing-run-failed-001`, Step-up 없는 Restore의 `STEP_UP_REQUIRED`를 확인했다.
- DOM width 1920, scrollWidth 1905, 가로 Overflow 0이며 Console warning/error는 0건이다.
- C02 Browser 문맥에서도 Resource Timing API가 가용하지 않아 `unavailable`로 기록하고 요청 0건으로 추정하지 않았다.
- C02는 Domain Authorization·부모 Guard만 변경했으며 Pane·CSS·시각 계약은 바뀌지 않았다. 따라서 새 PNG를 만들지 않고 C01 상태 PNG 12개와 네 폭 기준선 4개를 유지했으며 최종 Manifest에서 16개 모두 Hash·Signature·Image Decode를 재검증했다.
- 정확한 Production Test PID 59908만 종료하고 Port 4177 종료를 확인했으며 임시 stdout/stderr Log를 제거하고 Browser Session을 종료했다.

## 조치

- 변경: `packages/ui/src/operations-recovery-model.js`, `scripts/tests/operations-recovery.test.mjs`.
- 갱신: Operations/Recovery Adapter 계약, Domain JSON 2개, Browser JSON, Evidence Manifest, 기존 Progress.
- 변경하지 않음: Pane·CSS·Route·PNG, Dependency·Lockfile·Toolchain·CI, Navigation·Screen 정본, 실제 API·Queue·DB·Backup·Restore·Update·배포.
- 어울1은 현재 Worktree Diff와 31개 Manifest Artifact를 읽기 전용 재검토한 뒤 Commit·Push 여부를 판단해야 한다. 어울2는 이 보고 후 추가 쓰기를 중지한다.

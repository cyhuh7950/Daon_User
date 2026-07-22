COMPLETED | R1-M2-07-I001 | C01 불변 Run·Mode/상태 Fail-close·Recovery 정본 권한·직전 계보·Outcome·화면 증거 보정 | Model·Pane·전용 Test·Adapter 계약·Domain/Browser JSON·상태 PNG 12개·Manifest·Progress | C01 8/8 포함 전용 20/20·전체 순차 161/161·Lint·Build·Gate·Browser·Manifest PASS | C01 Browser Resource Timing unavailable, 실제 Adapter는 M4~M9 deferred_actual | 어울1 읽기 전용 재검토 후 Commit·Push 판단

# R1-M2-07 Correction 1 결과보고

## 판정

`COMPLETED` — Attempt 1의 `INCOMPLETE 1/3` 원인이었던 C2/C3 경계를 승인 범위 안에서 최소 보정했다. 신규 공격 Test 8건, 전체 회귀, 최종 Production Build·공통 Gate와 상태별 실제 Browser 증거가 모두 통과했고 미해결 C2/C3는 없다. Commit·Push·PR·Merge·외부 배포는 수행하지 않았다.

## 판단 이유

### Run 불변·계보

- `complete-retry-preview`는 현재 `actualRunCreated=false` Prototype 진행 Run에만 적용한다.
- `immutable:true`, 최초·과거·종료 Run, 다른 SourceVersion·역할 Run은 안정 Code로 거부하고 Source·Run·Queue·성공 Audit를 바꾸지 않는다.
- 두 번째 재처리는 최초 Run이 아니라 동일 SourceVersion·필수 역할의 직전 실패 재처리 Run을 `retry_of_processing_run_id`로 사용한다.
- 부모 Run 전체 객체는 새 Run 생성·완료 전후 깊은 불변임을 직접 비교했다.

### Mode·상태·Event·Outcome Fail-close

- Mode는 `auto | local_only | pinned | direct`, Outcome은 `ready | policy_blocked | runtime_exhausted`만 허용한다.
- 자동 Event는 Source가 정확히 `waiting_model`일 때만 평가한다.
- Unknown Mode, `ready | needs_review` Source, 동일 Event와 Unknown Outcome을 각각 `INVALID_SELECTION_MODE`, `SOURCE_NOT_WAITING_MODEL`, `DUPLICATE_TRIGGER_EVENT`, `INVALID_RETRY_OUTCOME`으로 거부한다.
- 거부 경로는 예외 없이 Event·processed Event·Run·Queue·Source·성공 Audit의 관련 상태를 보존한다.

### Recovery AuthorizationContext

- Action Payload의 Role·Grant·`stepUpStatus`·임의 승인 문자열을 정본으로 사용하지 않는다.
- 현재 활성 Membership·Tenant·Workspace와 Restore/Rollback/Update별 Capability를 다시 검사한다.
- StepUpAuthorization은 Actor·Action·Target·Tenant·Workspace·PolicyVersion·`issued`·미만료·미사용을 모두 확인한다.
- G9 승인은 `drill | deploy`, Target·Scope·PolicyVersion·`approved`·승인자·승인 시각이 일치해야 한다.
- Viewer·권한 회수·다른 영역·Payload 권한 주입과 위조·만료·사용됨·Scope 불일치 Step-up/G9는 Preview·성공 Audit·외부 효과 0건이다.
- 유효 정본도 `RECOVERY_PREVIEW_ONLY`만 만들고 Step-up을 한 번 사용 처리하며 Audit에 두 정본 ID를 남긴다.

### TDD·자동 검증

| 검증 | 결과 |
| --- | --- |
| C01 최초 RED | 기존 12 PASS / 신규 C01 8 FAIL, 독립 검토 원인과 일치 |
| 전용 최종 | 20/20 PASS |
| 전체 선택 회귀 | `--test-concurrency=1` 161/161 PASS |
| Workspace Lint | 11 files PASS |
| Web Production Build | PASS; 7 Route |
| 공통 Quality Gate | Exit 0, failures 0; 7범주 모두 PASS |
| Manifest | 31/31 SHA-256·Byte PASS |

최초 병렬 전체 실행은 Workspace Lint의 공유 임시 Fixture 경합으로 159/161이었다. 같은 두 Test 단독 2/2와 전체 순차 161/161이 모두 통과해 제품 결함이 아님을 분리했고 Test를 삭제·완화·Skip하지 않았다. 공통 Gate가 갱신한 범위 밖 R1-M1-05 결과 2개는 판독 후 HEAD 정본으로 복원했다.

### 실제 Production Browser와 증거 역할

- Attempt 1의 `operations-*.png` 4개는 네 폭 반응형·Typography·Overflow 기준선으로만 보존했다. C01 상태 주장에 재사용하지 않았다.
- 신규 12개 PNG가 재처리 계보·중복 Event, 장애 6종, Step-up/G9 3종, 알림 Count 2·recovered·Deep Link, 500px Compact 읽음을 직접 증명한다.
- `browser-validation.json`은 주장별 PNG, 직접 표시 문자열, 최종 Pixel·Byte·SHA-256을 12/12 연결한다.
- Chrome 실제 클릭으로 `/operations`, `/notifications`, `/operations?incident=incident-alert-002`를 확인했고 Deep Link 뒤 `recovered`가 보존됐다.
- 500×900에서 DOM width 500, scrollWidth 485, 가로 Overflow 0이었다. Console warning/error는 0건이다.
- C01 실행 문맥은 Resource Timing API를 노출하지 않아 `unavailable`로 기록했다. 요청 0건으로 추정하지 않았으며 Attempt 1의 가용 Timing 결과는 별도 baseline으로만 남겼다.
- Browser 반환 JPEG 바이트는 보이는 내용 변경 없이 실제 PNG로 재인코딩하고 Signature·Image Decode를 확인했다. 시각 검토에서 React Paint 전 Frame을 발견한 알림 PNG는 다시 수집했으며, 읽음 직접 PNG 주장은 실제로 표시된 500px Compact 파일에만 연결했다.
- 정확한 Production Test PID 90756만 종료하고 Port 4177 종료를 확인했으며 내부 Test 주소가 든 Server Log는 증거에 남기지 않았다.

## 조치

- 변경: `packages/ui/src/operations-recovery-model.js`, `packages/ui/src/operations-recovery-pane.jsx`, `scripts/tests/operations-recovery.test.mjs`.
- 갱신: Operations/Recovery Adapter 계약, Domain JSON 2개, Browser JSON, Evidence Manifest, 기존 Progress.
- 신규: C01 상태 PNG 12개와 이 Correction 보고서.
- 변경하지 않음: Dependency·Lockfile·Toolchain·CI, M2-01~06 구현, Navigation·Screen 정본, 실제 API·Queue·DB·Backup·Restore·Update·배포.
- 어울1은 현재 Worktree Diff와 31개 Manifest Artifact를 읽기 전용 재검토한 뒤 Commit·Push 여부를 판단해야 한다. 어울2는 이 보고 후 추가 쓰기를 중지한다.

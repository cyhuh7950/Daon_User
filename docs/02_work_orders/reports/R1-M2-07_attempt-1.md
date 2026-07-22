COMPLETED | R1-M2-07-I001 | 운영·알림·복구 Production-bound Prototype 구현 | Operations/Notifications Route·Domain Model·Pane·Test·Adapter 계약·Browser/Domain 증거·PNG 4개·Progress·Manifest | 전용 12/12·선택 회귀 153/153·Lint·Build·공통 Gate·실제 Browser PASS | 실제 API·Queue·DB·Backup·Restore·Update·배포는 후속 Adapter 소유 | 어울1 읽기 전용 검토 후 Commit·Push 단계 판단

# R1-M2-07 Attempt 1 결과보고

## 판정

`COMPLETED` — 승인된 작업 범위 안에서 `/operations`·`/notifications`, 운영 상태·알림·`waiting_model` 재처리·장애별 축소·복구 Preview를 구현했다. 자동·회귀·Build·공통 Gate와 실제 Production Browser 검증이 모두 통과했고 미해결 C2/C3 결함은 없다. Commit·Push·PR·Merge·서버 배포는 수행하지 않았다.

## 판단 이유

### 운영·알림 정본

- API·Worker·DB·Object Storage, 처리·Index·실패·자동·수동 Queue, Local·Internal·External Model과 Runtime Node를 단일 ViewState로 투영한다.
- Daon·인터넷 Connector, 사용자·조직 저장·Token·비용 한도, Backup 검증·RPO/RTO·Restore Drill, Update Channel·Rollback 가능 여부를 안전 Metadata로 표시한다.
- Alert는 안정 Key로 활성 신호를 한 항목 Count로 집계하고 복구 후 새 세대·Incident로 분리한다.
- Incident는 `detected→warning→restricted→recovering→recovered` 순서를 강제하고 알림·Audit·선택 상태를 함께 보존한다.

### `waiting_model` 자동·수동 재처리

- `auto`와 허용된 `local_only`만 필수 역할의 Deployment·Node·Provider `ready/healthy` Event에서 새 Run Preview 한 건을 Queue한다.
- `pinned`·직접 선택은 Readiness Event만으로 실행하지 않고 `MANUAL_RETRY_REQUIRED`를 표시한다.
- 수동 경로는 현재 Membership·Capability·Tenant·Workspace·Source ACL을 다시 검사하며 Payload Role·Grant 주입과 권한 회수·다른 영역을 거부한다.
- 실패한 이전 Run은 불변이고 새 Run에 `retry_of_processing_run_id`, Trigger, 현재 ACL·영역·RoutingPolicyVersion·비용·Egress Snapshot을 남긴다.
- Event·Idempotency·활성 Run·Backoff 중복은 변경 0건으로 억제한다. 결과는 Ready, 정책 후보 0, Runtime 재소진을 구분한다.

### 축소 운영·보안

- Daon·External LLM·Local LLM·인터넷·Index·Evidence Store의 여섯 장애를 독립 Fixture로 검증했다.
- 각 장애는 승인된 범위만 축소하고 무단 Fallback·다른 Service 변경·실제 외부 효과를 만들지 않는다.
- 비용 한도, Step-up 실패·만료, 과거 결과 AccessDecision 신호를 표시한다.
- Restore·Update Preview는 각각 `STEP_UP_REQUIRED`, `G9_DRILL_APPROVAL_REQUIRED`, `G9_DEPLOY_APPROVAL_REQUIRED`를 직접 표시하며 실제 실행은 0건이다.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| 최초 TDD RED | 전용 0/12, 미구현 Model·Route 원인 일치 |
| 전용 최종 | 12/12 PASS |
| 지정 선택 회귀 | 153/153 PASS |
| Workspace Lint | 11 files PASS |
| Web Production Build | PASS; `/operations`, `/notifications` 포함 7 Route |
| 공통 Quality Gate | Exit 0, failures 0; lint·type·unit·contract·build·security·independence PASS |
| 최종 `git diff --check` | 오류 0건 |
| 금지 URL·직접 Fetch·민감 문자열 Source Scan | 0건 |

Quality Gate가 자동 갱신한 범위 밖 R1-M1-05 결과 파일은 판독 후 현재 HEAD 정본으로 복원했다. Dependency·Lockfile·Toolchain·CI는 변경하지 않았다.

## 실제 Production Browser

- Chrome에서 새 Production Build의 `/operations`, `/notifications`, Incident Deep Link를 실제로 열고 클릭했다.
- 1920×1080, 1200×900, 800×900, 500×900 Viewport 모두 Route·Screen ID가 맞고 문서 가로 Overflow 0, 본문 12px, H1 16px였다.
- 자동 Queue·중복 Event·`pinned` 수동 제한·수동 계보, 장애 6종, Incident 복구, 알림 읽음·Deep Link, Step-up/G9 경계, Alert Count 2, Queue Filter 화면 왕복 보존을 직접 문자열로 확인했다.
- Tooltip은 열림 1건 후 Escape로 0건이 되었고 Console warning/error는 0건이었다.
- Browser Resource Timing API를 실제 조회해 비동일 Origin 0건과 API-like Resource 0건을 확인했다. 정적 검사로 0을 추정하지 않았다.
- Browser 반환 Screenshot은 JPEG Signature였으므로 보이는 내용 변경 없이 실제 PNG로 재인코딩했다. 최종 PNG Signature와 IHDR을 검증했으며 Viewport와 스크롤바를 제외한 Screenshot Content Pixel을 Browser JSON에 함께 기록했다.

## 생성·변경 결과

- 신규 Route: `apps/web/app/operations/page.jsx`, `apps/web/app/notifications/page.jsx`
- 신규 Domain/UI: `packages/ui/src/operations-recovery-model.js`, `packages/ui/src/operations-recovery-pane.jsx`
- 최소 연결: `packages/ui/src/index.js`, `packages/ui/src/workspace.css`
- 전용 Test: `scripts/tests/operations-recovery.test.mjs`
- 계약: `docs/01_architecture/operations_recovery_prototype_adapter_contract.md`
- 증거: Domain JSON 2개, Browser JSON, 실제 PNG 4개, Evidence Manifest
- 운영 기록: 지정 Progress와 이 결과보고

## 미해결 사항·다음 판단

- 실제 API·Auth·DB·Migration·Queue·Worker·Object Storage·Model·Connector·Notification Write·Backup·Restore·Update·Rollback은 수행하지 않았다. 이는 미진이 아니라 계약에 명시한 M4~M9 `deferred_actual` 경계다.
- 실행 기준은 `ab2a3b055581fcaea75cceafc3bb8bedb2a80066` 위 미Commit Worktree다.
- 어울1은 허용 범위 Diff와 증거를 읽기 전용 검토한 뒤 Commit·Push 및 다음 작업을 판단해야 한다.

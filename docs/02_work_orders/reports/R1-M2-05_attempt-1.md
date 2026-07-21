COMPLETED | R1-M2-05-I001 | Studio 생성·Version·검토·승인·전달·등록·모바일 흐름 Prototype 구현 | Studio Domain·Pane·Workspace 최소 연결·Test·Architecture·Browser Evidence | 자동 73/73, Quality Gate·Production Build·Browser 네 폭 PASS | 실제 API·DB·LLM·파일·전달·Index는 M4/M5/M6/M8 deferred_actual | 어울1 검토 후 S9 Commit·Push·격리 서버 검증

# R1-M2-05 attempt 1 결과보고

## 판정

`COMPLETED` — 승인된 R1-M2-05 Prototype 범위와 필수 산출물·로컬 검증을 충족하여 `HANDOFF_READY`다. Commit, Push, 외부 배포, 실제 API·DB·LLM·파일·전달·Index 성공은 수행하거나 주장하지 않았다.

## 수행한 작업

- 다섯 산출물 Tile과 유형별 필수 Section·허용 형식을 구현했다.
- 7개 생성 설정 범주, 완화 불가 정책 잠금·사유, 설정 확정·무효화·재확정·명시 제출, 깊은 불변 `GenerationSettingsSnapshot`을 구현했다.
- 불변 OutputVersion·previous Version·Revision 이유·근거 계보와 비교 화면을 구현했다.
- ReviewRequest와 ApprovalRequest를 분리하고 수정 요청, 반려 새 Draft, 만료, 회수, 재요청, 승인 후 변경·재승인을 구현했다.
- Export·Delivery·KnowledgeRegistration마다 현재 Membership·ACL·SourceVersion·조직 정책을 다시 판정하는 `AccessDecision`과 안정적 안전 Code를 구현했다.
- 역할 Matrix와 모바일 Allowlist를 순수 판정 함수로 구현하고 Content Revision과 Review·Approval·Read 상태를 분리했다.
- M2-02 Workspace 상태·반응형 Projection, M2-03 Evidence Viewer·SourceVersion, M2-04 Run 계보 계약을 재작성하지 않고 연결했다.

## 생성·변경한 결과

### 신규

- `packages/ui/src/studio-workflow-model.js`
- `packages/ui/src/studio-workflow-pane.jsx`
- `scripts/tests/studio-workflow.test.mjs`
- `docs/01_architecture/studio_workflow_prototype_adapter_contract.md`
- `docs/03_evidence/release_1/R1-M2-05/browser-validation.json`
- `docs/03_evidence/release_1/R1-M2-05/studio-settings-1920x1080.png`
- `docs/03_evidence/release_1/R1-M2-05/studio-approved-delivery-1200x900.png`
- `docs/03_evidence/release_1/R1-M2-05/studio-mobile-matrix-800x900.png`
- `docs/03_evidence/release_1/R1-M2-05/studio-mobile-blocked-500x900.png`
- `docs/03_evidence/release_1/R1-M2-05/evidence-manifest.json`
- `docs/04_test_reports/release_1/R1-M2-05_progress.md`
- `docs/02_work_orders/reports/R1-M2-05_attempt-1.md`

### 최소 변경

- `packages/ui/src/adaptive-workspace.jsx`: 기존 Studio 면에 전용 Pane 연결
- `packages/ui/src/workspace-model.js`: `studio_workflow` 상태 Slice와 Action 연결
- `packages/ui/src/index.js`: Studio Domain·Pane 공개 Export
- `packages/ui/src/workspace.css`: Studio UI와 네 폭 반응형 Style
- `scripts/tests/workspace.test.mjs`: Workspace 상태 정본의 Studio Slice 회귀 기대값

## 테스트 결과

| 구분 | 명령·검증 | 결과 |
| --- | --- | --- |
| TDD RED | `node --test scripts/tests/studio-workflow.test.mjs` | 최초 미구현 계약 0 PASS / 9 FAIL 확인 |
| 최신 전수 자동 검증 | Studio + Workspace + Workspace Lint + Source + Run + Foundation 6개 Test 파일 | Exit 0, 73/73 PASS |
| Workspace | `npm run verify:workspace` | Exit 0, 34/34 PASS |
| Foundation | `npm run verify:product-foundation` | Exit 0, 8/8 PASS |
| Lint | Workspace Lint 직접 실행 | Exit 0, 11 files PASS |
| Production Build | `npm --workspace @daon-user/web run build` | Exit 0, Compile·TypeScript·Static Page 3/3 PASS |
| 공통 Gate | `npm run verify:quality-gate` | Exit 0, Overall PASS, failures 0; lint 4, type 1, unit 4, contract 1, build 4, security 2, independence 1 |
| Diff | `git diff --check` | 오류 0건 |
| 금지 URL | Studio Browser Source 집중 검색·정적 Test | 절대주소·localhost·127.0.0.1·`NEXT_PUBLIC_API_BASE_URL`·직접 fetch 0건 |

Quality Gate가 범위 밖 M1-05 증거 2개를 자동 갱신했으나 착수 시 Clean이었던 정확한 두 파일만 원복했다. 보호 Dirty인 M1-04 `dependency-graph.json`, `violations.json`은 수정·복원·Stage하지 않았다.

## Browser 결과

- Production Process와 새 Browser Session에서 실제 클릭했다.
- 1920×1080 `three-pane`, 1200×900 `two-pane`, 800×900 `single-pane`, 500×900 `bottom-tabs` 실제 inner viewport를 확인했다.
- 생성 설정 확정→변경 무효화→재확정→제출, Draft 편집→검토→수정 요청→승인, 승인 Version의 부분 마스킹 Delivery, 현재 접근 차단, 명시 등록, 모바일 허용·차단을 확인했다.
- Console Warning 0건, Error 0건이다.
- Browser 평가 문맥에서 `performance`가 제공되지 않아 Resource Timing은 `unavailable`과 원인을 기록했다. API-like·비동일 Origin·금지 주소 요청 수를 0으로 위장하지 않았다.
- 네 PNG는 화면 겹침·가로 잘림·상태 초기화가 없음을 시각 검수했다.

## 오류·원인·복구

1. 첫 Production Build에서 `packages/ui/src/index.js`의 Studio Export 중복으로 실패했다. 장시간 Patch 호출을 중단한 뒤 실제 적용 여부를 재확인하지 않고 같은 Patch를 다시 적용한 것이 원인이었다. 중복 2줄만 제거하고 같은 Build를 재실행하여 Exit 0을 확인했다.
2. Browser Screenshot API의 `path` 인수가 모바일 재수집에서 파일을 생성하지 않았다. 반환된 PNG `Uint8Array`를 지정 증거 경로에 명시 저장하고 네 파일의 존재·크기·SHA-256을 다시 확인했다.

## 미해결 사항과 다음 판단

- `deferred_actual`: 실제 Studio API·DB 저장, Provider·LLM 실행, DOCX·PDF·XLSX·CSV·JSON·SVG·PNG 생성·Open, Delivery, Knowledge Index, 실제 역할 계정·Native Gateway·기기 검증은 M4/M5/M6/M8 책임이다. 이번 PASS 수에는 포함하지 않았다.
- 어울1은 변경 Diff와 증거를 읽기 전용 검토한 뒤 S9 Commit·Push 및 exact SHA의 ysna-server 격리 검증 진행 여부를 판단해야 한다.
- 현재 Source 기준은 `70c34c466a56e27b0d0d5079b38f673bc34dd12c` 위의 미Commit Worktree 변경이다.
- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M2-05_progress.md` (최종 SHA-256은 Manifest 작성 직전 확정)

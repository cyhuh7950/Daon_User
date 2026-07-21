# 작업 결과보고서 `R1-M2-02-C01` · Attempt `2`

## 판정

`HANDOFF_READY` · Attempt 1 검토의 8개 결함을 결함별 TDD Red부터 수정하고 C0~C7 자동 회귀·실제 Chrome·Evidence Hash·Diff 검증을 완료했다. 구현 쓰기를 중지하고 어울1의 Diff 검토·Commit·Push 및 불변 SHA 전달을 대기한다.

## 필수 결과 필드

| 필드 | 결과 |
| --- | --- |
| status | `HANDOFF_READY` |
| issue_id | `R1-M2-02-I001` |
| 수행한 수정 | Resize를 Pair 합계 기반으로 제한해 양쪽 Pane 20~55와 합계 불변을 보장했다. Modal 최초 Focus·Tab Trap·명시 inert·Trigger Stack·Drawer→Viewer 중첩 복귀를 구현하고 실제 Hover/Focus/Touch-Click Tooltip을 추가했다. Resize Hit Area와 Control은 M2-01 Token을 사용한다. `/`는 Home Shell, 동적 Workspace Route만 Prototype으로 정정했다. TypeScript 7 CLI Parse와 정적 규칙을 수행하는 별도 `lint:workspace` 및 Negative Fixture Test를 추가하고 Lint/Unit/Build Gate 의미를 분리했다. 동적 Route와 정확 DOM data 검사를 Test에 포함했다. |
| 테스트 결과 | C1 Red는 Workspace 7건+Lint 2건, 총 9건으로 결함을 재현했다. 최종 Workspace 14/14, Product Foundation 8/8, 기존 Gate Test 25/25, Lint 정상 8 Files와 비정상 Fixture Exit 1, Toolchain PASS, Independence Violations 0, Next Production Build·Typecheck PASS, 공통 7범주 Gate 전부 PASS·Failures 0·Exit 0이다. `git diff --check`, 추적 삭제 0, Lockfile·M2-01 정본·허용 범위 검사와 Evidence Hash 6건도 PASS했다. |
| Browser 증거 | 실제 Chrome 1920×1080에서 Resize `[30,38,32]→[48,20,32]→[20,48,32]`, 합계 100과 Hit 24px를 확인했다. 1200×900은 Drawer/Viewer 최초 Focus, Tab·Shift+Tab 순환, inert, Viewer→Drawer Evidence Trigger와 Drawer→Launcher 복귀를 확인했다. 800×900 Drawer Focus/inert/Escape, 500×900 Bottom Tab·44px Touch·Touch Help·500px 전체화면 Viewer·Focus Trap·`page-18:paragraph-2` 복원을 확인했다. `/`는 `home/home`, Workspace는 `workspace_detail/workspace_detail`이다. 실제 DOM 값은 `run-prototype-unavailable`, `artifact-evidence-report-draft`, `evidence-source-page-12`, Seed `page-12:paragraph-4`, 이동 후 `page-18:paragraph-2`다. Console Error 0, API Resource 0, non-same-origin 0, 금지 Target 0이다. |
| 미해결 사항 | S9 GitHub CI·ysna-server ARM64 및 S10 최종 Evidence 검증은 Commit·Push 불변 SHA가 없어 시작하지 않았다. 운영 배포·PR Merge는 어울2 권한 밖이다. 작업 전부터 존재한 R1-M1-04 Evidence Dirty 2건은 보호했으며 본 작업에 포함하지 않는다. |
| 다음 판단 | 어울1이 C01 Diff를 검토해 Commit·Push하고 불변 SHA를 전달할지 판단한다. 전달 후 같은 어울2가 S9 GitHub Hosted Runner·ysna-server 격리 검증과 S10 최종 결과를 수행한다. |

## 판단 이유

- Pair 합계에서 이웃 최소폭을 역산해 대상과 이웃을 동시에 Clamp하므로 큰 양수·음수와 반복 입력에서도 음수나 20 미만·55 초과가 발생하지 않는다.
- Modal 배경과 Modal을 분리하고 배경에 실제 `inert`·`aria-hidden`을 적용했다. Modal Mount 후 최초 Control로 Focus를 이동하며 최상위 Modal 안에서 Tab을 순환한다.
- Trigger ID Stack과 `return_drawer`로 Viewer를 닫을 때 Drawer 문맥과 내부 Evidence Trigger를 복원하고, Drawer를 닫을 때 원 Launcher로 복원한다.
- Tooltip은 `aria-expanded`, `aria-describedby`, `role=tooltip` 관계를 가지며 Pointer Hover/Click, Keyboard Focus, Escape와 Blur를 실제 Chrome에서 확인했다.
- CSS Query의 1439·1023·599 복제는 Browser Media Query 제약 때문이며 JSON Token 6개 경계와 자동 대조한다. Hit Area와 Control 값은 Token 변수를 직접 소비한다.
- TypeScript 7 Root Export가 Version 정보만 제공해 최초 Compiler API 시도가 `ScriptTarget undefined`가 된 사실을 확인했다. 새 Dependency 없이 동등한 고정 TypeScript 7 CLI `--noCheck` Parse와 별도 정적 규칙 Runner로 복구했다.
- Chrome 첫 1200 재검증에서 발견한 `body` Focus 결함은 단일 RAF가 Commit보다 먼저 실행되고 Modal 없음 분기에서 pending Focus를 처리하지 않은 원인이었다. Modal Mount `autoFocus`, Commit 후 pending Trigger 처리와 명시 inert로 수정해 실제 Chrome에서 재검증했다.

## 조치

- 현재 상태: `HANDOFF_READY`.
- 구현 쓰기: C7 종료와 동시에 중지.
- Commit·Push·PR: 어울1 수행.
- 다음 단계: 불변 SHA 전달 후 S9 GitHub CI·ysna-server ARM64·자원 불변 검증과 S10 최종 Evidence를 수행한다.

## 변경 파일

- Web: `apps/web/package.json`, `apps/web/next.config.mjs`, `apps/web/app/`.
- UI: `packages/ui/package.json`, `packages/ui/src/`.
- Package 경계: `packages/contracts/package.json`, `packages/design-tokens/package.json`.
- Gate·Test: `package.json`, `quality-gate-policy.json`, `scripts/lint-workspace.mjs`, `scripts/tests/workspace.test.mjs`, `scripts/tests/workspace-lint.test.mjs`.
- 문서·Evidence: `docs/01_architecture/workspace_layout_state_adapter_contract.md`, `docs/03_evidence/release_1/R1-M2-02/`, 진행 기록과 본 보고서.

## 검증 명령

- `npm run verify:workspace` → 14/14 PASS, Exit 0.
- `npm run verify:product-foundation` → 8/8 PASS, Exit 0.
- `node --test scripts/tests/quality-gate.test.mjs` → 25/25 PASS, Exit 0.
- `npm run lint:workspace` → 8 Files PASS, Exit 0; 비정상 Fixture Exit 1 Test PASS.
- `npm run verify:toolchain` → PASS, Exit 0.
- `npm run verify:independence -- --no-write` → Violations 0, Exit 0.
- `npm run build --workspace @daon-user/web` → Production Build·Typecheck PASS, Exit 0.
- `npm run verify:quality-gate` → 7범주 PASS, Failures 0, Exit 0.
- 실제 Chrome 네 폭·Home Route → PASS, Console Error 0, API Resource 0.
- `git diff --check`, 추적 삭제, Lockfile·M2-01 정본·허용 범위·Evidence Hash → PASS.

# R1-M3-01-C01 수정 작업지시서 — Hydration-safe Session 복원과 Evidence 결속

## 1. 수정 계약

| 항목 | 내용 |
| --- | --- |
| 원 작업 | `R1-M3-01` |
| 수정 작업 | `R1-M3-01-C01` |
| issue_id | `R1-M3-01-I001` 유지 |
| 판정 | 독립 검토 `REJECT(C2, 중대 미진)` |
| 개발자 | 동일 어울2 · 단일 Writer |
| Branch/Worktree | `codex/r1-m3-01` · `C:\tmp\Daon_User-r1-m3-01` |
| 기준 상태 | 원 작업지시 Commit `8eeb12b` 위 미Commit R1-M3-01 구현·Evidence 전체 보존 |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M3-01_progress.md`에 C01 구간 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-01-C01_attempt-1.md` |

원 R1-M3-01 작업지시서와 지정 정본 전체를 다시 적용한다. C01은 아래 두 결함만 수정하며 승인된 BFF 구조와 기존 Green을 다시 설계하지 않는다.

## 2. 확정 결함

### C2-1 저장 Session 재진입 Hydration 오류

- 현재 `ProductionBoundEvidenceHub`는 `useReducer` 초기화 중 `window.sessionStorage`를 읽는다.
- SSR은 기본 상태를 렌더하고 Browser 첫 Hydration Render는 저장된 iOS/unavailable/Check 상태를 렌더할 수 있어 HTML이 결정적으로 달라진다.
- 실제 재사용 M2 Session 탭에서 React hydration 오류가 관찰됐다.
- 깨끗한 새 탭 Console 0은 저장 상태가 있는 사용자 재진입 경로의 완료 증거를 대신하지 못한다.

### C2-2 Evidence Manifest 구현 결속 누락

- 현재 R1-M3-01 Manifest 9건은 Contract·JSON·PNG만 포함한다.
- 실제 변경 Web/UI 코드와 전용 Test가 SHA-256·Byte로 고정되지 않아 구현과 검증 증거의 동일성을 보장하지 못한다.

## 3. 필수 수정

### 3.1 Hydration-safe 복원

- Server Render와 Browser 첫 Hydration Render는 동일한 기본 `ProductionBoundEvidenceState`를 사용한다.
- `sessionStorage` 읽기는 Hydration 이후 Effect에서만 수행한다. `suppressHydrationWarning`으로 오류를 숨기지 않는다.
- 복원 완료 전 기본 상태가 저장값을 덮어쓰지 않도록 복원 완료 Gate를 둔다.
- 저장 Payload는 기존 `selected_client_type`, `selected_status`, `checked_journey_ids` 계약을 유지한다.
- 손상·부분·허용되지 않은 저장값은 기존 Model의 안전 Transition을 통해 기본값 또는 허용값으로 닫는다.
- M2 Model/Reducer, Navigation, Screen, Token 정본은 변경하지 않는다. 수정은 `production-bound-evidence-pane.jsx`와 필요한 Test에 한정한다.

### 3.2 재현 Test

- 수정 전 구조가 Server 기본 상태와 저장 상태 첫 Render를 다르게 만들 수 있음을 유효 RED로 고정한다.
- 수정 후 Reducer 초기화 중 `window/sessionStorage` 접근이 없고, 복원은 Effect 이후에만 발생함을 Test한다.
- 복원 완료 전 Persistence가 저장 Payload를 덮어쓰지 않음을 Test한다.
- 기존 iOS/unavailable/Check 상태 보존 Test를 삭제·완화하지 않는다.

### 3.3 실제 Chrome 재진입

1. 깨끗한 Production Chrome에서 Home을 연다.
2. UI로 iOS·unavailable과 Journey Check를 선택한다.
3. 다른 Route 왕복 또는 같은 Origin 재진입·Reload로 저장 Session 복원 경로를 실제 실행한다.
4. 선택값·Check가 보존되고 Console warning/error 0인지 확인한다.
5. Shell `ready`, Downstream `deferred_actual`, same-origin BFF 1건과 가로 Overflow 0을 재확인한다.

Browser 저장소의 값을 직접 읽어 완료를 주장하지 말고 실제 UI 선택·재진입·표시·Console로 검증한다.

### 3.4 Manifest 재생성

다음 범주를 모두 Artifact로 포함해 현재 파일의 SHA-256·Byte를 고정한다.

- Web 구현: Layout, Runtime Descriptor, Route Handler
- UI 구현: Index Export, Runtime Model, Runtime Status, Runtime CSS, 수정된 Production Evidence Pane
- Test: Web Runtime Shell 전용 Test와 C01 Hydration 재현 Test가 별도라면 둘 다
- Contract
- Runtime·Browser·Lifecycle JSON
- PNG 5개와 C01 저장 Session 재진입 Screenshot
- C01 결과보고

Manifest 자체와 계속 갱신되는 Progress는 순환 Hash 대상에서 제외하고 `mutable_handoff_records`로 명시한다. `base_commit`, `worktree_changes_included=true`, 환경·실제/Fixture/Deferred 경계, 품질 결과를 명시한다. 모든 Artifact는 전수 Hash·Byte 검증 `N/N`이어야 한다.

## 4. 허용·금지 범위

추가 허용:

- `packages/ui/src/production-bound-evidence-pane.jsx`
- 기존 또는 신규 R1-M3-01 전용 Test
- 기존 R1-M3-01 Browser JSON·Screenshot·Manifest·결과보고·Progress
- C01 결과보고

계속 금지:

- M2 Model/Reducer, Navigation, Screen, Token 정본 변경
- BFF 경로·응답 의미·Dependency·Lockfile·Toolchain·CI 변경
- 실제 Backend·DB·LLM·외부 효과
- `suppressHydrationWarning`, Console 오류 필터링, 저장 상태 삭제로 회피
- 깨끗한 탭만으로 저장 Session 재진입 검증을 대체
- 관련 없는 Refactor 또는 전체 재작성

## 5. 수행 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| C01-S0 | 원 결과·독립 REJECT·현재 Diff·Process/Port 0 확인 | Progress |
| C01-S1 | 저장 Session Hydration 재현 Test 선작성 | 유효 RED |
| C01-S2 | Hydration-safe 초기화·복원·Persistence Gate 최소 수정 | 전용 Green |
| C01-S3 | 전용·전체 192+ 회귀·Lint·Build·Gate | 전부 PASS |
| C01-S4 | 실제 Production Chrome UI 선택→재진입·Reload·Console 0 | Browser JSON·PNG |
| C01-S5 | 종료·Port 0, Manifest 전체 재생성·N/N 전수 검증 | Evidence·Progress |
| C01-S6 | C01 결과보고 작성 후 쓰기 중지 | 정식 상태 반환 |

각 단계의 착수·완료·오류·복구·테스트·종료 직전에 기존 Progress를 갱신한다. 어울2는 Commit·Push·PR·ysna-server 배포를 수행하지 않는다.

## 6. 완료 조건

- 저장 Session 재진입 경로의 React hydration warning/error 0
- iOS/unavailable/Journey Check 실제 UI 상태 보존
- 기존 BFF·실패·재시도·same-origin·정보 비노출 계약 유지
- 전용·전체·Lint·Build·Quality Gate PASS
- 제품 코드·Test·Contract·JSON·PNG·C01 보고가 Manifest에 포함되고 Hash·Byte 전수 일치
- Lockfile·Dependency·Toolchain·CI·승인 정본 Diff 0
- 최종 제품 Process·검증 Port 0, 외부 효과 0, DB Migration N/A

결과보고 첫 줄:

```text
COMPLETED | R1-M3-01-I001 | C01 Hydration-safe 복원·Evidence 결속 | 변경 파일 | 테스트·Browser·Manifest 근거 | 미해결 사항 | 어울1 재검토 요청
```

COMPLETED | R1-M3-01-I001 | C01 Hydration-safe 복원·Evidence 결속 | Production Evidence Pane·Hydration Test·Browser/Lifecycle Evidence·Manifest | 전용 29/29·전체 196/196·Build·Gate·저장 Session Chrome·Manifest 21/21 PASS | 미해결 없음 | 어울1 재검토 요청

# R1-M3-01-C01 Attempt 1 결과보고

## 판정

COMPLETED. 독립 검토의 C2-1 저장 Session Hydration 오류와 C2-2 Manifest 구현 결속 누락을 승인 범위 안에서 수정했다.

## 수행한 작업

- `ProductionBoundEvidenceHub` reducer 초기화의 `window/sessionStorage` 접근을 제거했다.
- Server와 Browser 첫 Hydration Render가 같은 기본 상태로 시작하게 했다.
- 저장 Session 복원을 Hydration 이후 Effect로 이동하고 기존 Model Transition만 사용했다.
- 복원 완료 전 Persistence가 기존 Payload를 덮어쓰지 않게 Gate를 추가했다.
- Journey ID는 배열만 허용하고 중복을 제거한다. 손상·부분·금지 값은 기존 Transition으로 fail-close한다.
- `suppressHydrationWarning`, Console 필터링, 저장 삭제, Model/Reducer 수정은 사용하지 않았다.

## 변경 파일과 영향 범위

- 제품: `packages/ui/src/production-bound-evidence-pane.jsx`
- Test: `scripts/tests/web-runtime-shell-hydration.test.mjs`
- Evidence: Browser/Lifecycle JSON, Manifest, `web-shell-c01-session-reentry.jpg`
- 기록: 기존 Progress와 본 C01 보고서
- 원 Web Shell/BFF, M2 Model/Reducer, Navigation, Screen, Token, Dependency, Lockfile, Toolchain, CI는 변경하지 않았다.

## TDD와 자동 검증

- RED: 4개 중 3개 실패, 기존 Model fail-close 1개 통과. 실패는 initializer 저장 접근, 복원 Effect 부재, Persistence Gate 부재와 일치했다.
- GREEN: Hydration+기존 Platform/Web Shell 29/29 PASS.
- 전체 순차 회귀 196/196, Lint 11 files, Toolchain 7 manifests, Independence 53/0 PASS.
- Clean `.next` Fresh Production Build PASS, 8 routes.
- 공통 Quality Gate 7 categories PASS, failures 0.

## 실제 Chrome 저장 Session 재진입

- 실제 UI로 `iOS`, `unavailable`, 첫 Journey `확인 표시`를 선택해 `1/8 확인`을 만들었다.
- Workspace를 실제 클릭하고 Browser Back 뒤 선택값과 Check 보존을 확인했다.
- Reload 뒤 `ios`, `unavailable`, `1/8 확인`, 첫 Journey `확인됨` 보존을 다시 확인했다.
- 저장소 값을 직접 읽지 않았다.
- Reload 후 Console warning/error 0, Shell `ready`, Downstream `deferred_actual`, 가로 Overflow 0.
- Page Assets 9건은 모두 same-origin이며 정적 자산 8건과 BFF fetch 1건이다.
- C01 Screenshot은 1894×863, 162733 byte다.

## 오류와 복구

- 기존 `.next/trace-build`와 `.next/trace` EPERM이 두 Build에서 재현됐다. 관련 Process/Port 0 확인 후 ignored 산출물을 C:\tmp에 보존 이동하고 Clean Build로 PASS했다.
- 최초 standalone 정적 자산 복사에서 `-LiteralPath` 와일드카드가 확장되지 않았다. Source 12/Target 0을 확인하고 잘못된 Target을 C:\tmp에 보존 이동한 뒤 전체 Directory 복사로 12개·805542 byte 일치를 확인했다.
- 정적 자산 생성 전 기동된 PID 59108은 Chunk 404를 계속 반환해 종료했다. 동일 Build를 PID 83216으로 재기동한 뒤 Chunk 200과 Chrome 검증을 완료했다.
- 최종 PID 83216, 자식 89436, 4179/4180 Listener 모두 0이다.

## Evidence 결속

- Manifest는 `base_commit`, `worktree_changes_included=true`, 환경, Actual/Fixture/Deferred 경계와 품질 결과를 명시한다.
- 제품 코드·Test·Contract·JSON·PNG/JPG·본 보고서 21건의 SHA-256·Byte를 21/21 전수 확인했다.
- Manifest 자체와 Progress는 순환 Hash에서 제외하고 `mutable_handoff_records`로 명시했다.
- 외부 효과 0건, DB Migration N/A.

## 미해결 사항과 다음 판단

- C01 승인 결함 범위의 미해결 사항 없음.
- Backend, DB, LLM, Source, Delivery는 원 계약대로 `deferred_actual`이다.
- Commit, Push, PR, Merge, ysna-server 배포는 수행하지 않았다.
- 어울1의 독립 재검토와 최종 완료 판단이 필요하다.

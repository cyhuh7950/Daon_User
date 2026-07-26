# React Native 공용 Shell 계약

## 목적과 소유 경계

R1-M3-04는 Android와 iOS가 함께 소비하는 React Native 공용 화면·상태·Adapter 경계를 제공한다. `android/`, `ios/`, 설치 Package, Signing, OS Deep Link 등록과 Device Lifecycle은 각각 R1-M3-05·06 소유다. Public API Schema·인증·Tenant·권한·Network Gateway는 M4 소유이며, 연결 전 기본 상태는 `unavailable`이다.

## Contract Projection

- Host가 `android | ios`를 명시한다. OS나 화면 폭으로 Client Type을 추론하지 않는다.
- `packages/contracts/navigation.json`에서 Client 허용과 `native_route_key`가 모두 있는 Route 8개만 같은 순서로 투영한다. `organization_settings`, `operations`는 Mobile Navigation에 포함하지 않는다.
- 선택·Deep Link·Back은 허용된 `native_route_key`만 받는다. 미허용 입력은 현재 Route를 보존하고 안정 오류 Code를 반환한다.
- `packages/contracts/screens.json`에서 Screen과 `loading`, `empty`, `ready`, `warning`, `error`, `forbidden`, `unavailable` 일곱 상태를 직접 소비한다. 알 수 없는 상태는 `error`이며 `ready`로 승격하지 않는다.

## UI·Token·접근성

- 공용 Shell은 React Native 기본 Component만 사용하며 DOM UI, React DOM, Next, Web CSS, Browser API와 `packages/ui`를 Import하지 않는다.
- `@daon-user/design-tokens/tokens.json`의 px 값을 명시적 Adapter에서 Device-independent number로 변환한다. 본문·폼 12, 설명 10, 보조 9, Sidebar 제목 14, 화면 제목 16, Touch Target 44를 보존한다.
- 모든 Text는 OS 글꼴 확대를 허용한다. Pressable은 접근성 Label·Role·선택 상태를 제공한다. 상태는 색상 외 Icon과 Text를 함께 제공한다.
- 설명은 상시 Box가 아니라 `InfoActionAdapter`를 통해 Host Popover/Modal로 연결한다. Adapter가 없으면 `INFO_ACTION_HOST_UNAVAILABLE`이다.

## Domain·Public API 경계

- `PublicApiClient`는 Host가 이후 주입하는 Interface이며 Base URL, `fetch`, Auth Token 저장, Refresh, Tenant와 권한 구현을 포함하지 않는다.
- 기본 Client는 `NATIVE_PUBLIC_API_UNAVAILABLE`, `unavailable`, 교체 Owner `R1-M4-01`을 반환한다.
- 잘못된 Adapter 결과와 예외는 `NATIVE_ADAPTER_RESULT_INVALID`·`error`로 정규화한다.
- 모바일 Studio 15개 작업은 `packages/ui/src/studio-workflow-model.js`에서 생성·검증된 플랫폼 중립 Contract를 소비한다. Content 수정 3개, Workflow 6개, 차단 6개의 Code·Revision·Web/Windows 이어서 작업 의미를 보존한다. 이는 UX 경계이며 M4 Native Gateway 전까지 보안 강제를 주장하지 않는다.

## Build·검증 경계

- React Native `0.86.0`, React `19.2.7`, TypeScript `7.0.2`와 호환 의존성을 정확 Pin한다.
- `@daon-user/mobile`의 표준 `lint`, `type`, `unit`, `contract`, `build`는 Workspace 실행 위치에서 Shell 비종속 `npm --prefix ../..`로 Root의 대응 `verify:mobile-*` 구현을 호출한다. Root Package를 Workspace로 가장하지 않으며 Root 검증 Script의 단일 소유와 비순환 호출을 유지한다.
- 공통 Gate는 `npm run <표준명령> --workspace @daon-user/mobile`을 직접 실행해 Package 진입점 파손이 Root 우회 명령으로 가려지지 않게 한다.
- Metro Headless Production Bundle은 공용 Entry를 Android와 iOS 조건으로 각각 Transform·Minify할 수 있음을 검증한다.
- Bundle PASS는 Native Project·설치·Device·Simulator·Public API 성공이 아니다.
- 현재 Worktree의 Lockfile 재현성은 `npm ci --ignore-scripts`, Toolchain, Audit, Mobile Type/Bundle과 공통 Gate가 소유한다. 과거 Evidence Successor 계보는 고정 Origin·Successor Commit Blob과 Ancestor 관계로 별도 검증한다.

## 배포·후속 상태

- Git Commit·Push·PR·Merge·ysna-server: 어울1 후속
- Android Native Build·Device: Deferred R1-M3-05
- iOS Native Build·Device: Deferred R1-M3-06
- Public API·Auth·보안 재강제: Deferred M4
- DB Migration: N/A

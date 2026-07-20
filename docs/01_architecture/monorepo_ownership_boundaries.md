# Monorepo 소유·의존 경계

## 목적

Release 1의 Client·Service·공용 패키지가 후속 Work Order에서 독립적으로 구현·Build될 위치와 소유 책임을 고정한다. 기계 판독 정본은 루트 `repo-boundaries.json`이다.

## 사용자 흐름과 Runtime 경계

| 사용자 흐름 | 진입 Runtime | 연동 경계 | 내부 구현 금지 |
| --- | --- | --- | --- |
| Web Cloud-sync | `apps/web` Browser | same-origin 상대 경로 → Server-side BFF → 공개 API | Browser의 API 절대주소·내부 Host·Service Source Import |
| Windows Cloud-sync | `apps/desktop` Tauri Shell | 공개 Gateway Contract | API Service 내부 Source Import |
| Windows Local-private | `apps/desktop` Tauri Shell | 승인된 IPC/Loopback Contract → `services/local-service` | Local Service Source Import·외부 Interface Listen |
| iOS·Android | `apps/mobile` React Native | Versioned HTTPS 공개 Gateway | DOM UI·Server·Local Service 내부 Source Import |

## 구성요소 소유

| 경계 | 소유 책임 | 후속 Build·구현 Work Order |
| --- | --- | --- |
| `apps/web` | Browser UI와 Server-side BFF | `R1-M3-01` |
| `apps/desktop` | Tauri Shell과 Local Service 수명주기 | `R1-M3-02` |
| `apps/mobile` | React Native 공용 Shell | `R1-M3-04` |
| `services/api` | 공개 API·AI Orchestrator·Cloud Adapter | `R1-M4-05` |
| `services/local-service` | Local-private Runtime·저장·Local Model Adapter | `R1-M3-03` |
| `packages/ui` | Web·Desktop 표현 Component | `R1-M2-01` |
| `packages/contracts` | 공개 Schema 원천 | `R1-M4-01` |
| `packages/design-tokens` | 플랫폼 중립 Design Token | `R1-M2-01` |

## API·IPC 경계

- Web Browser는 same-origin 상대 경로만 사용한다. 내부 주소는 BFF·Reverse Proxy 같은 Server 경계에서만 해석한다.
- Native Client는 버전이 명시된 HTTPS 공개 Gateway만 사용한다.
- Desktop과 Local Service는 권한·수명이 제한된 IPC/Loopback Contract로 연동한다. Source Import로 결합하지 않는다.
- Service 간 협력은 공개 Contract와 Runtime API/Event로 수행하며 상대 Service 내부 모듈을 Import하지 않는다.
- `packages/contracts`에는 Provider SDK, Runtime Adapter, Secret과 내부 Host를 넣지 않는다.

## 데이터 소유 경계

- `services/api`가 Cloud Workspace·Source·Run·Studio 정본 접근과 Cloud Adapter를 소유한다.
- `services/local-service`가 Windows Local-private 메타데이터·파일·Index·Run Queue 접근을 소유한다.
- App은 사용자 상호작용 상태와 안전한 Client Session만 소유하며 Cloud·Local 데이터 저장 구현을 직접 소유하지 않는다.
- `packages/ui`, `packages/contracts`, `packages/design-tokens`는 Runtime 데이터 정본을 소유하지 않는다.
- Local-private에서 Cloud로 이동은 후속 Sync 계약의 승인된 Copy/Publish로만 수행한다.

## 의존 방향

```text
apps/web ---------> packages/ui ---------> packages/contracts
     |                   |
     +-------------------+---------------> packages/design-tokens

apps/desktop -----> packages/ui, packages/contracts, packages/design-tokens
apps/mobile ------> packages/contracts, packages/design-tokens
services/api -----> packages/contracts
services/local-service -> packages/contracts
```

`packages/contracts`와 `packages/design-tokens`는 다른 구성요소에 의존하지 않는 Leaf 원천이다. App→App, Service→Service, App→Service의 내부 Source 의존을 금지한다. Runtime 연동은 이 Graph의 Source 의존과 별개인 공개 API·Event·IPC 경계다.

## Daon과 보안 경계

Daon은 `services/api`의 표준 Connector Adapter 뒤에서만 선택 연동한다. Client·공용 패키지·Local Service가 Daon 내부 DB·URL·패키지·파일 경로를 직접 참조하지 않는다. Secret과 내부 Endpoint는 Server 또는 Local secure runtime 경계 밖으로 노출하지 않는다.

## 이번 작업의 비구현 범위

Framework scaffold, 실행 코드, `package.json`, Workspace Manager, Lockfile, Toolchain Pin, 검사 Script, CI, API·DB·Queue·Connector 구현은 생성하지 않는다. 의존성 설치와 Build도 수행하지 않는다.

# Local Service Boundary

## 책임

Windows Local-private Runtime, 로컬 저장, Managed Local Model Adapter와 Loopback API/IPC Server 경계를 소유한다.

## 허용 의존

- `packages/contracts`

## 금지 의존

App·Cloud API Service·UI·Token 내부 Source를 Import하지 않는다. 외부 Interface Listen을 기본으로 열지 않고 Cloud 내부 주소나 Secret을 Client에 노출하지 않는다.

## 후속 Build

Packaged Local Service Shell과 보안 연결 골격은 `R1-M3-03`이 소유한다.

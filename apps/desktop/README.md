# Desktop Application Boundary

## 책임

Tauri 2 Windows Shell, Web 공용 React UI 호스팅, 서명된 Local Service 수명주기와 승인된 IPC/Loopback 연결 경계를 소유한다.

## 허용 의존

- `packages/ui`
- `packages/contracts`
- `packages/design-tokens`

## 금지 의존

다른 App과 Service 내부 Source를 Import하지 않는다. Local Service는 공개된 IPC/Loopback Contract로만 호출하며 외부 Listen을 요구하지 않는다.

## 후속 Build

Desktop Shell 독립 Build는 `R1-M3-02`, Local Service 연결 골격은 `R1-M3-03`이 소유한다.

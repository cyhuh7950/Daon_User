# Mobile Application Boundary

## 책임

iOS·Android React Native Client와 Native Navigation·권한·Lifecycle 경계를 소유한다. HTTPS 공개 Gateway 계약만 사용한다.

## 허용 의존

- `packages/contracts`
- `packages/design-tokens`

## 금지 의존

다른 App·Service 내부 Source와 DOM 기반 `packages/ui`를 Import하지 않는다. 내부 Host·Local Service·Provider에 직접 연결하지 않는다.

## 후속 Build

공용 Mobile Shell은 `R1-M3-04`, Android와 iOS 설치 Build는 각각 `R1-M3-05`, `R1-M3-06`이 소유한다.

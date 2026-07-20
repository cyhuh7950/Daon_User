# Shared UI Package Boundary

## 책임

Web과 Desktop이 공유하는 React 표현 Component 경계를 소유한다. Domain 실행·데이터 저장·API 주소·Secret·Server 구현은 소유하지 않는다.

## 허용 의존

- `packages/contracts`
- `packages/design-tokens`

## 금지 의존

App·Service 내부 Source를 Import하지 않는다. React Native Mobile UI는 이 DOM Component 패키지를 직접 의존하지 않는다.

## 후속 Build

Production-bound 공용 UI 기준은 `R1-M2-01`이 소유하고 Web·Desktop 실행 Build에서 소비한다.

# Design Tokens Package Boundary

## 책임

색·간격·Typography·반응형·상태 Token의 플랫폼 중립 원천을 소유한다. Web·Desktop·Mobile은 플랫폼 Adapter를 통해 소비한다.

## 허용 의존

다른 저장소 구성요소에 의존하지 않는 Leaf 원천이다.

## 금지 의존

App·Service·UI·Contract 내부 Source와 Runtime·API·데이터 접근 구현을 포함하지 않는다.

## 후속 Build

Design Token과 전체 IA 기준은 `R1-M2-01`이 소유한다.

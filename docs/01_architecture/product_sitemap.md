# 제품 Sitemap·화면 목록 계약

## 판정

`R1-M2-01`의 전역 IA 정본은 `packages/contracts/navigation.json`, 화면 목록 정본은 `packages/contracts/screens.json`이다.

## 전역 흐름

`홈 → 워크스페이스 목록 → 개별 워크스페이스`가 주 작업 진입점이다. 전달함·작업 이력·알림은 작업 결과와 요청을 연결하고, 모델·연결 설정과 계정·조직 설정은 정책 범위 안의 구성만 노출한다. 운영 상태는 조직 관리자와 운영자에게만 노출한다.

모든 Route는 Web Pattern과 Native Route Key를 함께 가지며 URL에는 Tenant 비밀값·내부 주소·Provider 원시 식별자를 넣지 않는다. Android·iOS는 Capture·조회·질문·검토 중심 범위이며 조직 설정·운영 상태는 Web·Windows 책임이다.

## 상태와 Mock 경계

모든 화면은 `loading`, `empty`, `ready`, `warning`, `error`, `forbidden`, `unavailable`을 지원한다. M2-01은 기능 화면을 구현하지 않으므로 Adapter가 준비되지 않은 상태를 `unavailable`로 명시한다. `screens.json`의 `mock_boundary.adapter`와 `replacement_owner`가 후속 Production 구현의 교체 경계다.

## M3 승계

Web·Windows·Mobile Shell은 `route_id`, Native Route Key, 화면 제목 Key, Client·Role·Capability, 상태 집합을 폐기하지 않고 승계한다. 플랫폼별 Navigation 표현은 달라도 Route 의미는 동일하게 유지한다.

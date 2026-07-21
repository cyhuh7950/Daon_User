# Design Token·접근성 기준

## 판정

플랫폼 중립 정본은 `packages/design-tokens/tokens.json`이다. Web CSS Variable은 `tokens.css`, TypeScript Export는 `tokens.ts`이며 자동 Test가 정본과의 값 일치를 검증한다. Dark Theme 구현은 이번 범위가 아니다.

## 기준

- Typography: 본문·Form 12px, 작은 설명 10px, 보조 9px, Sidebar 제목 14px, 화면 제목 16px.
- 반응형: 1440px 이상, 1024~1439px, 600~1023px, 599px 이하.
- 상태는 색만으로 구분하지 않고 Label·Icon·Text를 함께 사용한다.
- `border` Palette는 비상호작용 구분선 전용이다. Control 경계는 3:1을 만족하는 `interactive_boundary`, Focus는 `focus_indicator` Semantic 역할을 사용한다.
- Reduced Motion에서는 전환 시간을 제거하거나 축소한다.

## 접근성 계약

`packages/ui/accessibility-contract.json`은 WCAG 2.2 Level AA, Keyboard Navigation, 가려지지 않는 Focus, Hover·Focus·Touch Tooltip, Icon-only Accessible Name, OS 글꼴 확대와 Screen Reader Label을 고정한다. 오류·경고·진행·권한 차단은 Tooltip에만 숨기지 않는다.

## M3 승계

Web·Windows는 CSS Adapter를, React Native는 JSON/TypeScript의 플랫폼 Adapter를 사용한다. DOM Component를 React Native에 강제로 공유하지 않으며 Token 이름과 접근성 의미만 공유한다.

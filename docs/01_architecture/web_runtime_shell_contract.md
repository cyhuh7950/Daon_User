# Web Runtime Shell 계약

## 목적

R1-M3-01 Web Shell의 `ready`는 Next Production Process와 same-origin BFF Shell 경계가 응답할 수 있다는 뜻만 가진다. Backend, DB, LLM, Source, Delivery Adapter의 준비 상태를 뜻하지 않으며 이 Downstream은 계속 `deferred_actual`이다.

## 실행 및 요청 경계

- Web은 기존 `apps/web`의 Next `output: standalone` Production Build를 사용한다.
- Browser Component는 정확한 상대 경로 `/bff/shell/runtime`만 GET 한다.
- Route Handler는 Server 경계의 Runtime Descriptor를 호출하며 Downstream Network를 호출하지 않는다.
- GET 응답은 `code`, `ready`, `shell_version`, `build_id`, `downstream_state`, `observed_at`만 공개하고 `Cache-Control: no-store`를 사용한다.
- POST, PUT, PATCH, DELETE는 405와 안전한 고정 응답으로 거부한다.
- Browser Source와 DOM에는 내부 Host, Port, Secret, 환경변수 값, Raw 오류를 포함하지 않는다.

## 상태 및 실패 계약

- 최초 조회 전은 `starting`, 성공은 `ready`다.
- 재조회 중 마지막 성공이 있으면 `recovering`이며 현재 성공으로 표시하지 않는다.
- 조회 실패 시 마지막 성공 Descriptor는 보존하지만 `ready=false`, `retryable=true`로 표시한다.
- 사용자는 작은 상태 표식의 `i` 버튼으로 설명을 열고 Escape로 닫으며, 실패 시 재시도할 수 있다.
- `Downstream: deferred_actual` 표시는 성공 상태에서도 유지한다.

## 승계 및 비범위

Navigation, Screen, Design Token과 M2 Model/Reducer는 변경하지 않는다. 실제 Business API, 인증, Tenant, DB Migration, Queue, LLM, 파일, Export, Delivery 및 Windows/Android/iOS Shell은 이 작업 범위가 아니다. 외부 효과와 DB Migration은 각각 0건과 N/A다.

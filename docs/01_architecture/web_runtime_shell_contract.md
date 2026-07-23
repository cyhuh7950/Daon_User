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

## Evidence 이식성 계약

- UTF-8 Text Artifact는 strict UTF-8 byte round-trip을 먼저 확인하고 `CRLF → LF`만 적용한 `portable_utf8_lf` 표현으로 SHA-256과 Byte를 함께 고정한다.
- Lone CR, Unicode 정규화, Trim, 공백 변경, JSON 재직렬화 등 다른 변환은 허용하지 않는다.
- PNG/JPG Binary Artifact는 byte를 변환하지 않는 `raw` 표현으로 SHA-256과 Byte를 함께 고정한다.
- Manifest Artifact마다 `representation`을 명시하고, Validator는 미지원 표현·파일 부재·UTF-8 실패·Hash 불일치·Byte 불일치를 모두 fail-close한다.
- Artifact 경로는 `/` 구분자의 실제 Repository 상대 Canonical 경로만 허용한다. `.`, `..`, 선행 `./`, 중복 구분자, 역슬래시, 대소문자·Symlink·Junction 별칭과 Root 밖 실제 경로는 Canonical Real Path 기준으로 fail-close한다.
- Canonical Real Path 중복을 거부하고 일반 파일만 허용한다. Windows 경로 중복 비교는 대소문자를 접는다.
- Manifest 자체와 계속 갱신되는 Progress는 순환 Hash 대상에서 제외하고 `mutable_handoff_records`로 선언한다.
- Linux 서버 검증은 Git이 포함된 `node:24.18.0-bookworm`, npm `11.12.1`, 일회성 `--rm` Container를 사용한다.
- Mount된 `/workspace`에만 `git config --global --add safe.directory /workspace`를 적용한다. `bookworm-slim`, 기존 서비스·Network·Volume·DB는 사용하거나 변경하지 않는다.

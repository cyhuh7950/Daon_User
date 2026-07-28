# R1-M4-02-C01 안전 Projection Endpoint 검증 보완 작업지시서

## 승인 기준

- 기준 Branch `codex/r1-m4-02`, 기준 HEAD `fa7f7aa1b3663f98243e1924be009b8fcdda9637`.
- R1-M4-02 승인 설계·작업지시와 Audit Event Core Architecture 계약을 유지한다.
- 어울2가 이 Worktree의 유일한 Writer다.

## 중대 보완 목표

안전한 JSON Key 아래 값이라도 Loopback·Private·Link-local·Unspecified·Reserved IP, Docker/Internal/Local Host와 이를 가리키는 absolute 또는 scheme-relative Endpoint를 append 전에 fail-close한다. 공인 IP·공인 Domain·일반 문자열은 허용해 부분 문자열 기반 과잉 차단을 만들지 않는다.

## 허용 범위

- `services/api/src/daon_user_api/audit.py`
- 관련 `services/api/tests/test_audit*.py`
- Audit Architecture·R1-M4-02 결과보고·결정론 증거
- 본 Work Order·Prompt·Progress와 C01 결과보고

OpenAPI·다른 Package·App·UI·DB·HTTP·Workflow·Lockfile·외부 의존성은 변경하지 않는다.

## 구현 계약

1. 값 전체가 raw IPv4/IPv6 주소이면 `ipaddress`로 의미를 판정한다. IPv6 bracket 표기도 안전하게 정규화한다.
2. `urlsplit`로 scheme이 있는 URL과 `//host/path` scheme-relative URL의 Host를 추출한다. http/https에 한정하지 않고 postgresql 등 다른 Scheme도 같은 Host 정책을 적용한다.
3. Hostname은 정확한 `localhost`, Docker 내부 Host와 `.internal`·`.local` 경계를 거부한다. 일반 문장·공인 Domain에 금지 문자열이 일부 포함됐다는 이유만으로 거부하지 않는다.
4. Loopback·Private·Link-local·Unspecified·Reserved IP를 IPv4·IPv6 모두 거부하고 공인 IP는 허용한다.
5. 오류는 안정 Code만 제공하고 입력 문자열·Credential·Secret 원문을 반사하지 않는다.
6. Python 3.14.3 표준 라이브러리만 사용한다.

## TDD·검증

- 기존 구현에서 raw IP와 non-http 내부 Endpoint가 통과하는 RED를 먼저 기록한다.
- 최소 회귀: raw IPv4/IPv6의 Loopback·Private·Link-local·Unspecified·Reserved, bracket IPv6, non-http URL, scheme-relative URL, internal/local Host를 거부한다.
- 공인 IPv4/IPv6·공인 Domain·정상 일반 문자열은 Append 성공을 검증한다.
- 기존 Audit 전체와 `verify:api-audit -- --write`/no-write, Python Compile·Package Export, Independence·Workspace·Toolchain·관련 Quality Capability를 직접 검증한다.
- 실패 시 exact base 비교하고, 변경 범위상 전체 장시간 Quality Gate는 기존 R1-M4-02 근거를 사용한다.

## 진행·종료

`docs/04_test_reports/release_1/R1-M4-02-C01_progress.md`에 착수·RED·GREEN·검증·종료 직전 상태를 기록한다. `판정 → 판단 이유 → 조치` 결과보고 후 같은 Branch에 단일 목적 Commit을 추가 Push하고 Local/Remote SHA·Clean을 확인한다. PR·CI·Merge는 어울1 소유다.

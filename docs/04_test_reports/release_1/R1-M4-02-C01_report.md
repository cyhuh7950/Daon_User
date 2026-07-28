# R1-M4-02-C01 안전 Projection Endpoint 검증 보완 결과보고

## 판정

`COMPLETED` — 독립검토에서 확인한 내부 Endpoint 값 검증 누락과 부분 문자열 과잉 차단을 승인 범위 안에서 보완했다.

## 판단 이유

- 기존 구현은 http/https URL에만 Host 정책을 적용해 raw IP, non-http와 scheme-relative 내부 Endpoint를 통과시켰다.
- 기존 금지 Host 부분 문자열 검사는 `notlocalhost.example`과 일반 문장도 거부했다.
- 수정 구현은 표준 `ipaddress`·`urlsplit`과 정확한 Host/Host:Port 문법으로 값 전체의 Endpoint 의미를 판정한다.
- IPv4·IPv6의 Loopback·Private·Link-local·Unspecified·Reserved와 Docker·`.internal`·`.local` Host를 append 전에 거부한다.
- 오류는 `AUDIT_VALIDATION_FAILED:UNSAFE_JSON_VALUE`만 반환하고 입력값을 반사하지 않는다.
- 공인 IPv4·IPv6·Domain·Endpoint와 일반 문장을 허용해 과잉 차단을 방지한다.

## 테스트 결과

| 검증 | 결과 |
| --- | --- |
| C01 RED | 위험 Endpoint 17건 미거부, 정상 문자열 2건 과잉 거부 재현 |
| 전체 Audit | 13/13 PASS |
| C01 Matrix | 위험 Endpoint 22건 거부·공인/일반 값 12건 허용 PASS |
| Audit Evidence write/no-write | PASS, Contract SHA `F859FE6645E312AB6E33F8C621EE54EFB262C480FA3F584469BAC83D812DE041`, Source SHA `B460D70311B99B734751982CA63C5F8FE558507E18AE12D5140C560663572B80` |
| Python Compile·Package Export | PASS |
| Workspace·Independence·Toolchain·Node Syntax | 34/34 PASS·133 Files/0 Violations·PASS·PASS |

## 조치

- R1-M4-02 Architecture·결과보고·결정론 증거를 C01 최종 상태로 갱신한다.
- 어울1은 추가 Commit의 원격 SHA를 기준으로 PR·CI·Merge를 판단한다.
- 장시간 전체 Quality Gate는 R1-M4-02에서 확보한 37/37 PASS를 사용하며, C01은 Audit Capability와 독립성 등 변경 범위 검증을 직접 통과했다.

## 미해결 사항

- C01 승인 범위의 미해결 사항 없음.
- 실제 HTTP·DB·Authorization 경계는 변경하지 않았고 후속 Work Order 소유다.

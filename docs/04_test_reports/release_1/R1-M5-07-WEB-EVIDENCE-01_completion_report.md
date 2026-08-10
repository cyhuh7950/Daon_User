# R1-M5-07-WEB-EVIDENCE-01 완료보고

## 판정

`BLOCKED / RESOURCE_UNAVAILABLE`

## 판단 이유

- 최초 검증의 `AUTHENTICATION_REQUIRED` 후 신산님이 Chrome 로그인 완료를 명시했고, 같은 읽기 전용 Work Order를 재개했다.
- 로그인 후 `Backup·Restore 실제 API`는 `working`에서 `failed · RESOURCE_UNAVAILABLE`으로 전이했다. 읽기 전용 `목록 새로고침` 1회도 같은 오류로 끝났다.
- Evidence Manifest는 과거 인증 차단과 현재 로그인 후 오류를 별도 `attempts`로 분리했으며, 현재 `screen.recovery_api_status`와 top-level verdict는 모두 `RESOURCE_UNAVAILABLE`을 가리킨다.
- 실제 화면은 계속 `Production-bound Prototype`, `실제 API 미실행`, `ready · Prototype Adapter 실제 외부 효과 0건`을 표시했다. 이 상태는 실제 Adapter 연결 성공이 아니다.
- Browser 공개 제어 표면에는 Network event/response API가 없고 Resource Timing도 evaluator에 노출되지 않아, 요구된 session·Backup 목록 URL·method·HTTP status 및 same-origin 직접 증명은 확보하지 못했다.
- 자격증명/Cookie/Storage 열람, Backup 생성, Restore Preview·Execute·Cancel, SSH·DB·Docker·직접 API 호출 없이 새 Tab을 닫고 Chrome 제어를 반환했다.

## 조치

1. 로그인 후 `RESOURCE_UNAVAILABLE` 화면, PNG·DOM 원본, Trace와 Network 도구 한계를 Evidence Pack에 기록했다.
2. 기존 `R1-M5-07` Manifest에는 본 보완 작업의 현재 차단 결과 link metadata만 갱신하고 기존 `EXTERNAL_DATA_PASS_BROWSER_EVIDENCE_PENDING` 판정을 유지한다.
3. 어울1은 동일 Runtime의 Recovery API/BFF resource availability 원인을 승인된 서버 운영 절차에서 판단하고, Browser Network event/response 접근 방법을 제공하거나 재검증 범위를 지시해야 한다.

## 증거 범위

- 실제 Chrome 화면/DOM: 인증 차단 원본과 로그인 후 Resource-unavailable 원본 PNG·DOM을 모두 보존.
- Browser Network: session·Backup 목록 요청의 URL·method·status 미확보, same-origin·internal direct-call 0건 미증명.
- 정적/Build/자동 테스트: 이번 읽기 전용 Browser 증거 보완 작업에서는 재실행하지 않음.
- 제품/M5 Exit/TP-2/TP-5: 통과를 주장하지 않음.

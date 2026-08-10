# R1-M5-07-WEB-EVIDENCE-01 완료보고

## 판정

`PARTIAL / SCREEN_READY_EMPTY_LIST / NETWORK_NOT_PROVEN`

## 판단 이유

- 배포 보정 Runtime 제품 Commit/서버 Checkout `d0f0d0985120b78e8b6a0d32e22c69df12d3969e`, Web Image `sha256:117cfd39f5bce3a50392a99aca8037658a6c44f0a6b805e2c8966eb559345722`에서 신산님의 로그인 상태를 사용했다.
- 새 검증 Tab의 자동 Session→Recovery 초기화 뒤 `Backup·Restore 실제 API`는 `ready`였다. 허용된 읽기 전용 `목록 새로고침` 1회 뒤에도 `ready`, Backup 행/표 0건, 오류 Trace 0건이었다.
- 화면 PASS는 이전 `AUTHENTICATION_REQUIRED`와 `RESOURCE_UNAVAILABLE` 오류가 현재 attempt에서 재현되지 않고 Recovery 영역이 `ready`가 된 범위다. 실제 Backup 데이터 행 표시 PASS는 0건이라 주장하지 않는다.
- Browser 공개 제어 표면에는 Network event/response API가 없어 session·Backup URL·method·HTTP status, same-origin 및 내부 API 절대주소/localhost 직접 호출 0건은 `NOT_PROVEN`이다.
- 기존 두 실패 attempt 원본은 보존하고, 배포 보정 attempt의 PNG·DOM·관찰 기록을 별도 연결했다.
- 자격증명/Cookie/Storage 열람, Backup 생성, Restore Preview·Execute·Cancel, SSH·DB·Docker·직접 API 호출 없이 수집 직후 Tab/관련 제어 화면을 finalize해 Chrome 제어를 반환했다.

## 조치

1. 현재 화면 PNG·DOM 원본과 `SCREEN_READY_EMPTY_LIST / NETWORK_NOT_PROVEN` 관찰을 Evidence Pack의 3차 attempt로 추가했다.
2. 기존 `R1-M5-07` Manifest의 link metadata만 현재 결과로 갱신하고 기존 `EXTERNAL_DATA_PASS_BROWSER_EVIDENCE_PENDING` 판정은 유지한다.
3. 어울1은 화면 ready/빈 목록을 수용할지와 별도 Browser Network 원본 수집 수단을 제공할지 판단해야 한다.

## 증거 범위

- 실제 Chrome 화면/DOM: 과거 인증 차단·Resource-unavailable 및 현재 배포 보정 ready/빈 목록 원본 PNG·DOM을 모두 보존.
- Browser Network: session·Backup 목록 요청 URL·method·status 미확보, same-origin·internal direct-call 0건 미증명(`NOT_PROVEN`).
- 정적/Build/자동 테스트: 이번 읽기 전용 Browser 증거 보완 작업에서는 재실행하지 않음.
- 제품/M5 Exit/TP-2/TP-5: 통과를 주장하지 않음.

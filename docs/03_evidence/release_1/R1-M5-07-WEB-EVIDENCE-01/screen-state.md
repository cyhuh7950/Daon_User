# R1-M5-07-WEB-EVIDENCE-01 화면 상태

- 확인 시각: `2026-08-10T10:07:12+09:00`
- 대상 URL: `https://daon-user.sinsan.kr/operations`
- Browser: 신산님 로그인 Chrome의 새 검증 Tab 1개 (검증 직후 종료)
- Runtime 제품 Commit: `061bc4dcbddfd839fdcb64aa21ed498fe1e70e0b`
- Web Image: `sha256:e056da75e9d666249a21b48ebe645908758bc24494f84e58dab7c7086b760bc4`
- API Image: `sha256:91b454616ef6ee2c63ca6a02bcac59b576f0ccb1c4c6affc9830b66da38ba581`
- 서버 Checkout 문서 HEAD: `2fb74f1b54f520df2525559a3f8bf6cda06683d4` (Runtime Build SHA가 아님)
- 원본 PNG·DOM 재확인 시각: `2026-08-10T10:21:16+09:00` (같은 화면 상태, 새 Trace는 DOM 원문에 보존)

## 실제 표시 결과

1. 제목은 `운영 상태·복구`이고, 헤더는 `Production-bound Prototype`, `실제 API 미실행`을 표시했다.
2. 상태 영역은 `ready · Prototype Adapter 실제 외부 효과 0건`을 표시했다.
3. `Backup·Restore 실제 API` 영역에는 `목록 새로고침`, `전용 Fixture Backup 요청` 버튼이 있었으나 상태 변경 금지 경계를 따라 누르지 않았다.
4. 같은 영역의 실제 상태는 `failed · AUTHENTICATION_REQUIRED · Trace trace-bff-0edf20bb-95d8-4603-8856-08ce14d016d7`이었다.
5. 화면은 Backup `verified`, Restore Drill `passed_fixture · 실제 Restore 0건` 및 `G9-DRILL/G9-DEPLOY 승인 없는 실제 Backup·Restore·Update·Rollback은 0건`을 표시했다.

## 판정

`BLOCKED / AUTHENTICATION_REQUIRED`.

이 화면은 실제 Adapter 연결 성공 또는 Backup 목록/상세 요청 성공을 입증하지 않는다. 화면의 Prototype 표기와 인증 차단을 그대로 보존하며, M5 Exit/TP-2/TP-5 통과 또는 실제 Restore 성공을 주장하지 않는다.

## Screenshot 수집

Chrome 제어 세션에서 전체 화면 Screenshot을 읽기 전용으로 캡처해 `operations-authentication-required-2026-08-10.png` 원본 PNG로 보존했다. 같은 재확인 DOM Snapshot은 `operations-authentication-required-2026-08-10.dom.txt` 원문으로 보존했다. 인증 차단을 확인한 즉시 Tab/DevTools를 종료했으므로 인증 우회·추가 클릭·재열람은 하지 않았다.

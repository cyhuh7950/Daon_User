# R1-M4-03 Native Refresh Runtime 보정 실행 프롬프트

공식 작업공간 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`의 `AGENTS.md`와 `docs/02_work_orders/release_1/R1-M4-03-NATIVE-REFRESH-C02_work_order.md`가 지정한 승인 정본·현재 OpenAPI·Task 1 결과를 읽고 단일 Writer로 수행하라.

작업지시서의 TDD, 기존 `IdentityService.rotate_refresh` 단일 재사용, opaque Credential 비노출, Replay Family·Session 철회 Fail-close, Runtime·OpenAPI 일치, Refresh Route 한정 Idempotency 예외, 기존 Web·Native Login 보존과 허용 파일 경계를 준수하라. 별도 Refresh 로직·응답 캐시·Credential Log·Client Platform 입력으로 우회하지 마라. Commit·Push·배포·Browser·실제 Credential 사용은 수행하지 않는다.

종료 시 작업지시서의 표준 결과 계약으로 보고하라.

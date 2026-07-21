# 수정 작업지시 프롬프트 `R1-M1-05-C2`

당신은 Daon 사용자 프로그램 개발 Subagent 어울2다. 현재 R1-M1-05 Diff와 Correction 1 결과를 보존하고 다른 작업자의 변경을 되돌리지 않는다.

1. 원 작업지시·Correction 1과 `docs/02_work_orders/release_1/R1-M1-05_correction-2_work_order.md` SHA-256 `72C78AAF2A9C44F95051F191A7F9FAC03FE561EBF635239C485DB7E43D7C2A74`를 EOF까지 읽는다.
2. Correction 2만 Test-first로 수행하고 단계마다 기존 진행 복구 기록에 Append한다.
3. 동일 설치, Commit·Push·PR·서버 작업은 수행하지 않는다.
4. 완료 시 다시 `HANDOFF_READY`를 보고하고 코드 쓰기를 중지한다.

작업지시서 내용을 중복하거나 확장하지 않는다.

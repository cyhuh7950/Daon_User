# 수정 작업지시 프롬프트 `R1-M1-05-C1`

당신은 Daon 사용자 프로그램 개발 Subagent 어울2다. 같은 저장소에 다른 역할의 산출물이 있으므로 다른 작업자의 변경을 되돌리거나 정리하지 말고 R1-M1-05 허용 범위의 단일 Writer로만 작업한다.

1. 원 작업지시와 정본 전체를 다시 확인하고 `docs/02_work_orders/release_1/R1-M1-05_correction-1_work_order.md` SHA-256 `7655C7D45C22C74481DBE627B92A1ACBCBE20FEFD3F0A92536705D3E6E41E6BF`를 EOF까지 읽는다.
2. 현재 `HANDOFF_READY` Diff와 Evidence를 보존한 채 수정 작업지시 범위만 Test-first로 수행한다.
3. 진행 복구 기록에 Correction 착수부터 Red·수정·Green·최종 Hand-off를 단계마다 Append한다.
4. 완료 시 `HANDOFF_READY` 중간 보고 후 코드 쓰기를 중지한다. Commit·Push·PR·서버 검증은 수행하지 않는다.

수정 작업지시서 내용을 이 프롬프트에서 재서술하거나 확장하지 않는다.

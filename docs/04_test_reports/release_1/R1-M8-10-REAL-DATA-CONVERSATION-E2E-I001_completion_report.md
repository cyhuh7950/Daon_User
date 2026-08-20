# R1-M8-10-REAL-DATA-CONVERSATION-E2E-I001 완료 보고

## 판정

`PARTIAL / CODE_VERIFIED / ACTUAL_PROVIDER_TRANSPORT_PASS / PRODUCT_E2E_NOT_RUN`

## 판단 이유

- 좁은 일반대화 allowlist만 Source 없이 선택 Provider를 호출하며, 그 밖의 질의는 grounded context를 계속 요구한다.
- 기존 Question DTO·route·Provider selection·egress/Step-up·Notebook scope를 보존했다.
- 일반대화에도 egress payload 변환 결과와 승인 bytes 및 실제 Provider transport bytes를 exact 일치시켰다.
- 완료된 Question의 scoped request digest를 run과 함께 불변 저장하고, exact HTTP replay를 binding·Provider·egress·Step-up보다 먼저 반환한다. mismatch·legacy·cross-notebook은 fail-close한다.
- Source·Knowledge·Conversation·Studio 로드 실패는 서로의 ready 상태를 덮지 않으며 UI는 일반대화를 `근거 미사용`으로 구분한다.
- `근거 미사용`은 응답 모양이 아니라 최신 요청의 local intent provenance로만 표시하며 stale/reload 응답에서 추론하지 않는다.
- actual PostgreSQL에서 일반대화 lineage와 grounded Citation→Studio 저장을 검증했고, 대표 Upstage transport를 서버 내부에서 1회 확인했다.
- 그러나 새 코드의 외부 배포와 Credential 반입을 금지한 승인 경계 때문에 새 제품 경로 전체의 실제 Provider E2E와 1920×1080 Browser/Windows Gate는 실행하지 않았다.

## 변경 결과

- Domain/Runtime/PostgreSQL: general intent, selected Provider call, source-free lineage, fail-close grounded validation
- Web/Desktop/UI: 동일 allowlist, 기존 exact body, 독립 오류 상태, `일반 대화 · 근거 미사용`
- OpenAPI/BFF: 새 field/route 없이 semantic branch와 same-origin exact forwarding
- Boundary: production import graph의 fixture/test-harness 유입 차단
- Test/Evidence: disposable PostgreSQL Gate, remote Provider compatibility helper, transcript와 manifest

## 테스트 결과

- API focused `36 passed` + actual PostgreSQL `3 passed`
- Node related `87/87 passed`
- Rust Native wire `3/3 passed`
- 최종 Unicode 공통 벡터 focused: Python `1/1`, Node `1/1`, Rust `1/1` passed; fullwidth letter·`！`·`？`·U+3000은 fail-close, ASCII `!/?`는 허용
- Web build/boundary PASS, Desktop build PASS, lint PASS, OpenAPI verifier PASS
- actual Provider Upstage bounded 1회: HTTP 200/schema valid/secret echo0
- actual PG HTTP replay: current binding/provider/policy side effect0, mismatch write0, cross-notebook replay0
- 최종 Minor에서는 actual Provider/PostgreSQL/Windows 재실행0이며 기존 actual 판정은 변경하지 않았다.

## 미해결

- 새 제품 코드가 적용된 승인 환경에서 실제 Source 업로드→처리→일반 대화→grounded Citation→Studio 저장을 대표 Provider로 실행하지 않았다.
- 해당 흐름의 Browser 1920×1080 Network/console 및 Windows actual 증거가 없다.

## 조치

- 외부 배포 또는 격리된 승인 환경 적용 권한이 주어지면, Credential을 노출하지 않는 서버 내부 경계에서 미실행 actual 제품 Gate만 수행한다.
- 그 전에는 `COMPLETED` 또는 제품 E2E PASS로 판정하지 않는다.

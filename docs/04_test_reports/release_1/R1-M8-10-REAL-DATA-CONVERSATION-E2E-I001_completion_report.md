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

## 2026-08-21 재개 판정

`PARTIAL / POLICY_UI_CODE_VERIFIED / POLICY_DEPLOY_AND_STEP_UP_PENDING / PRODUCT_E2E_NOT_RUN`

- 운영 effective policy가 `deny_external`임을 read-only로 확인했다. 승인 범위와 다르므로 Provider actual을 실행하지 않은 것이 fail-close 계약과 일치한다.
- 기존 Workspace policy API의 500 결함과 Web save UI 부재를 TDD로 수정했다. 공개 API·DTO·데이터·보안 계약 변경은 0이다.
- 조직과 Workspace는 별도 단계·별도 ETag·별도 Step-up으로 저장하고, 활성 단계의 현재 비밀번호 입력 하나만 유지하며 성공·실패 후 즉시 비운다.
- Egress Node/BFF/React `6/6`, API Egress `10/10`, Web build·TypeScript·12 routes·boundary `391 files / violations0`, lint 3 files, OpenAPI exact PASS다.
- uncommitted source 직접 복사/배포는 금지되어 실제 서버 변경0이다. 어울1의 검토·exact stage·commit·push·Daon 전용 API/Web 배포가 다음 단계이며, 이후 사용자가 정식 화면에 현재 비밀번호를 입력해야 policy와 Provider actual Gate를 재개할 수 있다.
- 독립 리뷰의 async stale 위험을 monotonic epoch·AbortSignal·exact context/scope snapshot으로 닫았다. reverse load와 abort된 save는 최신 DOM·draft·ETag·error·password를 변경하지 않고 test adapter write0이며, save 중 scope navigation은 잠긴다.
- Step-up 호출은 두 scope의 target/operation/idempotency를 exact 검증했고 ACL deny consume0, wrong-target policy write0를 확인했다. fresh React/BFF `9/9`, API `10/10`, Web build/boundary `391/0` PASS다.
- Context 변경은 이전 policy DOM과 interaction을 즉시 0으로 만들고 새 load 성공 후에만 복구한다. Step-up 대기 중 abort는 policy POST0·sensitive clear를 보증한다. 이미 송신된 POST의 old snapshot write는 완료될 수 있으므로 write0으로 과장하지 않으며, stale UI projection0만 보증한다.
- Prop organization/workspace identity는 keyed wrapper가 stateful inner와 동기 결속하므로 passive effect 전 첫 commit에서도 이전 reducer/form/password/nav/text가 재사용되지 않는다. Empty props session-resolution은 유지하고 session GET에도 AbortSignal을 전달한다.
- REWORK3 fresh Gate는 focused Node `12/12`, API `10/10`, lint `4 files`, OpenAPI exact, Web build·TypeScript·12 routes·boundary `391/0` PASS다.

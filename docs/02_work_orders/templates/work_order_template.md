# 작업지시서 `{work_order_id}`

## 1. 문서 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `{work_order_id}` |
| 버전 / SHA-256 | `{version}` / `{hash}` |
| issue_id | `{issue_id}` |
| 상태 / 시도 | `READY` / `{attempt_no}` |
| 단일 Writer | 어울2 · `daon-developer` |
| 선행조건 | `{depends_on}` |
| 기준 Commit | `{baseline_commit}` |
| 상세 설계 정본 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` · `{hash}` |
| Release 1 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` · `{hash}` |
| 진행 복구 기록 | `docs/04_test_reports/release_1/{work_order_id}_progress.md` |
| 결과보고서 | `docs/02_work_orders/reports/{work_order_id}_attempt-{attempt_no}.md` |

작업자는 기준 문서와 이 작업지시서를 EOF까지 읽고 시작한다. 요약본은 정본을 대체하지 않는다. 실제 코드가 계획과 다르면 증거를 남기고 승인 경계를 넘지 않은 상태에서 어울1에게 보고한다.

## 2. 목표와 범위

- 단일 목표: `{single_goal}`
- 포함: `{in_scope}`
- 제외: `{out_of_scope}`
- 변경 허용 경로: `{allowed_paths}`
- 변경 금지 경로: `{forbidden_paths}`

요구되지 않은 리팩터링·구조 변경·전체 재작성·설정값 임의 변경·임시 운영 구조를 금지한다.

## 3. 구현 계약

`{design_requirements}`

Browser 코드는 same-origin 상대 경로만 호출한다. `localhost`, `127.0.0.1`, Docker 내부 Host/Port, `NEXT_PUBLIC_*` 내부 API 주소를 Client Fetch에 사용하지 않는다. 내부 주소는 Server/BFF/Proxy에서만 사용한다.

화면 작업은 1920×1080·본문 12px 기준과 10/9/14/16px 보조 규격을 지킨다. 설명 박스 상시 노출 대신 i 아이콘·Tooltip·Popover를 사용하되 필수 상태·오류·권한·경고는 보이는 UI로 제공한다.

## 4. 단계와 복구 기록

| 단계 | 작업 | 단계 완료조건 |
| --- | --- | --- |
| S0 | 기준 문서·Commit·작업 범위·기존 Dirty 상태 확인 | 시작 Snapshot 기록 |
| S1 | 영향 범위·회귀 위험·기존 테스트 확인 | 구현 전 판단 기록 |
| S2 | 요구 구현 | 변경 파일과 결정 기록 |
| S3 | 기본 테스트·정적 검사·Build | 명령·Exit Code·결과 기록 |
| S4 | 실제 Process·화면·Network/저장소 검증 | 증거 경로와 한계 기록 |
| S5 | Diff·무관 변경·완료조건 최종 대조 | 결과보고 작성 후 종료 Snapshot |

`docs/02_work_orders/templates/progress_template.md`를 복사해 지정된 진행 파일을 만들고 다음 시점마다 즉시 갱신한다.

1. 착수 직후
2. 각 세부 단계 완료 직후
3. 오류 발생 직후와 복구 직후
4. 테스트·Build·실제 검증 직후
5. 종료 또는 중단 직전

중단 후 재개자는 진행 파일의 마지막 `next_action`부터 이어서 수행한다. 비밀값·Token·원문 개인정보는 기록하지 않는다.

## 5. 테스트와 완료조건

- 필수 자동 검증: `{automated_checks}`
- 필수 실제 검증: `{runtime_checks}`
- 회귀 범위: `{regression_scope}`
- 완료조건: `{acceptance_criteria}`
- 증거 저장 경로: `docs/03_evidence/release_1/{work_order_id}/`

정적 검사·Build·HTTP 200과 실제 화면 클릭·Network·데이터 검증을 구분해 보고한다. 작업자가 기본 테스트와 확인을 완료하지 않은 결과는 `COMPLETED`로 보고할 수 없다.

## 6. 결과보고 계약

결과는 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나로 제출한다. 보고 형식은 `work_report_template.md`를 사용한다. 첫 오류만으로 실패보고하지 말고 원인·대안·현재 Diff·테스트를 조사한다.

- 중대한 미진: 핵심 완료조건 실패, 보안/데이터/공개계약 위반, 관련 회귀, 실행 증거 부재 → 별도 수정 작업지시서 후보
- 경미 보완: 핵심 완료조건과 회귀가 통과하고 외부 동작을 바꾸지 않는 문구·증거 정리 등 → 다음 작업지시서에 흡수 가능
- 사소한 보완만으로 합격 작업 전체를 다시 열지 않는다.

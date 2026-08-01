# R1-M6-04 작업지시서 — Device·Local Node·Relay

## 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-04` |
| Issue ID | `R1-M6-04-I001` |
| 버전 | 1.0 |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 기준 저장소 | `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User` |
| 설계 근거 | 상세 설계서 §11.4, §14.4, §16, §18.1 |
| 계획 근거 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md`의 R1-M6-04 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-04_progress.md` |

## 목적

독립 사용자 프로그램의 장치 Pairing·Device Identity·단기 인증서/키 회전·Local Node outbound-only Relay 인가와 장치 폐기 계약을 구현한다.

## 포함 범위

- tenant에 귀속된 장치 identity와 pairing 상태
- 짧은 수명의 인증서 발급·회전 및 이전 인증서 무효화
- Local Node의 outbound-only relay authorization
- 공개 inbound 경로 0건을 계약으로 강제
- 장치 revoke 이후 인증서·키·relay session 무효화
- 위 동작을 검증하는 단위 테스트와 진행·결과 증거

## 제외 범위

- 실제 물리 장치·OS keychain·HSM 연동
- 공개 API·브라우저 화면·외부 Relay 서버 배포
- 기존 `identity.py`의 사용자 인증·세션 공개 계약 변경
- ysna-server/Oracle Cloud 배포 및 DB migration

## 구현 계약

1. Pairing 완료 identity는 `tenant_id`, `device_id`, 공개키 digest, 현재 인증서 digest와 만료 시각을 가진다.
2. 인증서 TTL은 양수인 단기 값이어야 하며 회전 시 이전 인증서는 즉시 거부한다.
3. Relay는 `outbound`만 허용한다. `inbound` 또는 public inbound 요청은 `PUBLIC_INBOUND_FORBIDDEN`으로 거부한다.
4. relay 인가는 identity 상태가 `online`이고 현재 인증서가 유효할 때만 성공한다.
5. `revoked` 장치는 인증서 검증과 relay 인가 모두 실패해야 한다.
6. 키·인증서 원문과 pairing secret을 로그·예외·테스트 증거에 기록하지 않는다.
7. 이 Work Order는 새 공개 HTTP API를 추가하지 않으며 내부 Python 계약으로 제한한다.

## 허용 변경 파일

- `services/api/src/daon_user_api/local_node.py`
- `services/api/tests/test_local_node_relay.py`
- 본 Work Order의 진행·결과 문서

## 테스트 및 완료 증거

- TDD: 테스트 작성 → RED 실행 및 커밋 → 구현 → GREEN 실행
- Pairing/identity 생성, 인증서 회전, inbound 차단, revoke 후 무효화 각 1건 이상
- API 전체 unittest 회귀 실행
- 정적 검색으로 공개 inbound/절대주소/비밀값 로그가 추가되지 않았음을 확인
- 완료 시 `R1-M6-04_progress.md`와 결과보고서에 명령, 결과, 변경 파일, 미해결 사항을 기록

## 보고 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 보고한다. 설계·보안·공개 계약을 변경해야 하면 구현을 중지하고 어울1 판단 또는 신산님 승인을 요청한다.

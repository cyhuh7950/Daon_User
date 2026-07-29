# R1-M5-01-C01 완료보고서

## 판정

`COMPLETED` — R1-M5-01-C01의 DB 장애 Liveness·Readiness 분리와 Workspace-scoped Idempotency 보완을 완료했다. 정식 `FAILURE_REPORT` 누적은 0회다.

## 판단 이유

1. Cloud Pool은 Process 시작 시 DB 연결을 강제하지 않으며, DB가 내려간 상태에서도 API가 기동되어 Live 200과 Ready 503을 분리한다.
2. Readiness는 Worker Thread에서 bounded DB 점검을 수행해 같은 Event Loop의 Live 요청을 차단하지 않는다. DB·Migration 복구 후 같은 API Container와 시작 시각을 유지한 채 Ready 200으로 전환했다.
3. Idempotency PK·Advisory Lock·Replay Query·Audit Event ID가 Workspace를 포함한다. 같은 Tenant·Actor·Operation·Key라도 서로 다른 Workspace에서 각각 독립 성공하고 Audit·Idempotency가 각각 1건이다.
4. 실DB 11개 Test와 독립 Application Connection에서 다른 Workspace Row의 직접 Read·Write가 모두 0건이고 Pool Context가 제거됨을 확인했다.
5. PostgreSQL 18.4 격리 환경에서 Backup, Migration 재적용, Downgrade, Restore, 재Upgrade를 완료했다. Runtime 중 DB를 다시 내렸다 올린 뒤에도 같은 Process가 Live 200·Ready 503에서 Ready 200으로 회복했다.
6. 응답·Log의 내부 DB 정보 Hit는 0이고 종료 후 전용 Checkout·Container·Network·Volume은 모두 0이다.

## 조치

- 변경: `PostgresCloudStore` Lazy Pool·bounded Readiness, Async Event Loop 분리, 안전한 Pool 연결 실패 Log, Workspace-scoped Idempotency 계약.
- Migration: 운영 전 최초 `0001`의 Idempotency PK에 `workspace_id` 추가.
- Test: DB 부재 기동·Event Loop 비차단·동일 Tenant의 Workspace 격리 회귀 추가.
- 문서: Cloud 저장소 운영 기준과 C01 Evidence·진행기록 갱신.
- 검증 Code SHA: `f872e89f57c5771601eafc9e0a8e637e323d6f4d`.
- 다음 단계: 어울1의 독립 Diff·Evidence 검토와 다음 Work Order 진행 판단.

## 결과 계약

`COMPLETED | R1-M5-01-C01 | DB 장애 중 기동·동일 Process 회복과 Workspace Idempotency 격리를 보완 | Cloud Store·Runtime·0001 Migration·Test·Architecture·Evidence·Progress·완료보고 변경 | 로컬 전체 회귀·Quality 37개·ysna-server 실DB 11개·Backup/Restore·자원 정리 PASS | 미해결 제품 결함 없음 | 어울1 독립 검토 후 다음 작업 결정`

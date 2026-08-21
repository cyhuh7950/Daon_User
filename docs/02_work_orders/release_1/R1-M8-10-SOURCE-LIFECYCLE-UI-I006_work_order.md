# R1-M8-10-SOURCE-LIFECYCLE-UI-I006 작업지시서

승인 정본 `AGENTS.md`, R1-M5-06 Work Order, Release 1 상세 설계·구현·테스트 계획과 `2026-08-21-source-lifecycle-ui-design.md` 및 동명 작업계획을 EOF까지 적용한다.

어울2는 단일 Writer로 Source 상태 소유권 분리, append-only Notebook Source 연결 해제, 기존 삭제 요청 request/get/cancel의 same-origin UI를 TDD로 수직 완성한다. 삭제 요청 inventory/reference는 Browser가 만들지 않고 서버가 current tenant/workspace/source 정본에서 exact 6종을 산출한다. 기존 사용자 Source와 영구 Purge를 건드리지 않고 보호 dirty/untracked를 유지한다. 공개 계약은 승인된 unbinding 및 inventory 입력 제거로 제한하며 기존 보안·RLS·Audit·Step-up을 완화하지 않는다. 모든 필수 검증 뒤 결과 계약으로 보고하고 commit/push/deploy는 수행하지 않는다.

진행 복구 기록은 `docs/04_test_reports/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006_progress.md`다.

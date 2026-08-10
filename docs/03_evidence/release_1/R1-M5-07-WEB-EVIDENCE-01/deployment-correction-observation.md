# 배포 보정 후 읽기 전용 관찰

- attempt_id: `deployment_corrected_ready_empty_list_2026-08-10`
- observed_at: `2026-08-10T11:24:00+09:00`
- Runtime 제품 Commit / 서버 Checkout: `d0f0d0985120b78e8b6a0d32e22c69df12d3969e`
- Web Image: `sha256:117cfd39f5bce3a50392a99aca8037658a6c44f0a6b805e2c8966eb559345722`
- API Image: `sha256:91b454616ef6ee2c63ca6a02bcac59b576f0ccb1c4c6affc9830b66da38ba581`

## 관찰 순서와 결과

1. 새 Chrome 검증 Tab에서 `/operations`를 열자 `운영 세션 확인 중`에서 `Web Shell 준비`로 전이했다.
2. 자동 초기화 뒤 `Backup·Restore 실제 API` 상태는 `ready`였고 Backup 행/표는 0건이었다.
3. 허용된 `목록 새로고침`을 정확히 1회 눌렀다. 최종 상태도 `ready`, Backup 행/표 0건, 오류 Trace 0건이었다.
4. 상태 변경 기능인 `전용 Fixture Backup 요청`, Restore Preview/Execute/Cancel 등은 누르지 않았다.
5. 현재 PNG 281,865 bytes와 DOM Snapshot 5,597 characters를 원본으로 수집해 Evidence Pack에 보존했다.
6. 공개 Browser Client의 Network 원본 비지원 때문에 Session/Backup 요청 URL·method·HTTP status, same-origin, 내부 API 절대주소/localhost 직접 호출 0건은 `NOT_PROVEN`이다.
7. 수집 직후 Tab/관련 제어 화면을 finalize해 Chrome 제어를 반환했다.

## 판정

`SCREEN_READY_EMPTY_LIST / NETWORK_NOT_PROVEN`.

화면상 이전 `RESOURCE_UNAVAILABLE`은 해소됐다. 그러나 Backup 데이터 행이 실제로 0건이고 Network 원본도 없으므로, 실제 Backup 데이터 표시 또는 same-origin Network를 PASS로 판정하지 않는다. 기존 `screen-state.md`와 `network-observation.md`는 최초 인증 차단 원문으로 보존하며, 현재 상태는 이 파일과 Manifest의 3차 attempt/current 객체가 정본이다.

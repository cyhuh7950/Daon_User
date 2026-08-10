# R1-M5-07-WEB-EVIDENCE-01 Network 관찰

## 관찰 조건

- 시각: `2026-08-10T10:07:12+09:00`
- 대상: `https://daon-user.sinsan.kr/operations`
- 실행 경계: Chrome 새 검증 Tab, 읽기 전용 화면 관찰만 수행.
- 금지 준수: 로그인 입력, Cookie/Local Storage/Session 저장소 열람, Backup 생성, Restore Preview/Execute/Cancel, SSH/DB/Docker/API 직접 호출을 수행하지 않았다.

## 결과

| 확인 항목 | 관찰 결과 | 판정 |
| --- | --- | --- |
| Session 읽기 요청 URL·method·status | 인증 차단 전에 Browser Network에서 확보하지 못함 | `NOT_OBSERVED` |
| Backup 목록 읽기 요청 URL·method·status | 인증 차단 전에 Browser Network에서 확보하지 못함 | `NOT_OBSERVED` |
| same-origin `/bff/...` 또는 승인 공개 경로 | 실제 request URL을 확보하지 못했으므로 증명 불가 | `NOT_PROVEN` |
| Browser Client의 internal URL/localhost 직접 호출 0건 | 실제 request URL을 확보하지 못했으므로 증명 불가 | `NOT_PROVEN` |
| 화면이 표시한 API 상태 | `failed · AUTHENTICATION_REQUIRED` | `BLOCKED` |

Browser 제어 API에서 화면 평가용 `performance.getEntriesByType('resource')`를 읽기 전용으로 시도했으나 격리 evaluator에는 `performance`가 정의되지 않아 Resource Timing을 읽을 수 없었다. 이는 제품 Network 결과가 아니라 이 검증 도구 표면의 제약이다. Console log는 0 entries였다.

인증 만료/미인증으로 해석되는 화면 상태가 이미 확인됐으므로, 작업지시서의 우회 금지에 따라 목록 새로고침을 추가로 누르거나 별도 브라우저/직접 API로 대체하지 않았다.

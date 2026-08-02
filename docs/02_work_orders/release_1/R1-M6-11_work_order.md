# R1-M6-11 작업지시서 — 인터넷 Connector

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-11` |
| Issue ID | `R1-M6-11-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §9.2, §12.3, §18.2 |
| 계획 근거 | Release 1 계획 R1-M6-11 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-11_progress.md` |

## 목적

인터넷 검색·Safe Fetch를 안전하게 제한하고 Snapshot 계보를 보존한다.

## 계약

- HTTPS URL만 허용하며 사용자 정보·localhost·사설 IP·내부 호스트는 거부한다.
- Redirect 대상도 동일 SSRF 정책으로 재검사한다.
- Fetch 결과는 URL·게시 시각·조회 시각·License·Content Digest·Version을 가진다.
- 실제 네트워크 호출 없이 테스트 가능한 정책·Snapshot 계약으로 제한한다.

## 제외

실제 검색 Provider·외부 네트워크·브라우저 UI·배포.

## 허용 변경 파일

- `services/api/src/daon_user_api/internet_connector.py`
- `services/api/tests/test_internet_connector.py`
- 본 Work Order 진행·결과 문서

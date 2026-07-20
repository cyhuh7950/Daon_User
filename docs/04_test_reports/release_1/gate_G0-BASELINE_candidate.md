# G0-BASELINE 후보 판정 보고서

## 판정

`CONDITIONAL GO 권고 · 신산님 결정 대기`

## 판단 이유

- G0-DESIGN·G0-PLAN·TP-0은 승인/PASS다.
- 상세 설계 DOCX 47쪽 전 페이지와 접근성 검사를 완료했다.
- 문서 기준 Commit `c94e553f3a6aa7d062645391e838e7a555706914`과 Manifest 기록 Commit `f4d7f6afd07ccd5417b66f81e481615bf0ec9938`이 생성되었다.
- R1-D013~D020 설계 미정의는 전부 해소되었다.
- 작업지시서·비중복 프롬프트·진행 복구 기록·결과보고·Attempt Ledger·Evidence Manifest 계약이 준비되었다.
- 제품/운영 기준 4건은 승인 필요하고 외부 자격·장치 6건은 차단 상태가 명시되어 있다.

## 신산님 결정 요청

| ID | 어울1 권고 | 승인 시 기준 |
| --- | --- | --- |
| R1-D001 | 승인 | Windows 11 23H2+, Chrome/Edge 최신 2개 주 버전, Android 12+, iOS 17+ |
| R1-D003 | 승인 | Hybrid Pilot: 로컬/Windows Local-private + WSL 통합 + OCI Seoul 운영, On-prem 정식 배포 제외 |
| R1-D009 | 승인 | OCI Seoul, 삭제 유예 30일, Audit 1년, RPO 15분, RTO 4시간, Legal Hold 우선 |
| R1-D010 | 승인 | 파일 100MB, Workspace 20GB, 사용자 동시 Run 2, 조직 20, 동기 API p95 3초(비동기 제외) |

## 조건부 외부 차단

| ID | 차단 입력 | 진행 규칙 |
| --- | --- | --- |
| R1-D004 | OIDC IdP·Test Tenant/Client | M4 인증 Work Order 차단 |
| R1-D006 | LLM/ASR/Embedding/Reranker Allowlist·장비·Provider | 관련 M6 Work Order 차단 |
| R1-D007 | Daon Sandbox·Credential·호환 계약 | M6 Daon Connector 차단 |
| R1-D008 | 검색 Provider·License·Credential·허용 Domain | M6 인터넷 검색 차단 |
| R1-D011 | Windows/Android/Apple 서명·알림 계정 | 해당 설치·배포 Work Order 차단 |
| R1-D012 | Android/iOS 장치·macOS Build Host/CI | 해당 실제 증거 Work Order 차단 |

## 조치

신산님이 위 4개 기준과 조건부 외부 차단 진행을 승인하면 G0-BASELINE 승인 ID를 발급하고 Manifest를 `APPROVED`, 구현 상태를 `READY`로 바꾼다. 그 다음에만 R1-M1-01 작업지시서와 비중복 작업지시 프롬프트를 작성해 어울2에게 전달한다. 승인 전에는 개발 Subagent를 실행하지 않는다.

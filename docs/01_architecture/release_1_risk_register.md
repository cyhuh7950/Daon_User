# Release 1 초기 위험 대장

| Risk ID | 위험 | 가능성/영향 | 조기 신호 | 완화·검증 | 소유자 | 현재 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| RSK-001 | 4개 플랫폼과 Local/Cloud 인프라가 R1에 집중 | 높음/높음 | CP1~CP3 지연, 플랫폼별 Mock 증가 | CP1~CP5·RC 순차 Gate, CP3 전 확장 중지 | 어울1 | Open |
| RSK-002 | macOS·Apple 서명 환경 부재 | 높음/높음 | M3 iOS Archive 불가 | R1-D011·D012 확보 전 iOS WO `BLOCKED`, 대체 산출물로 합격 금지 | 신산님·어울1 | External blocked |
| RSK-003 | Local LLM 하드웨어·모델 License 미확정 | 높음/높음 | 설치 실패·품질/SLO 미달 | M0 장비 Matrix, 모델 Allowlist, M6 실제 Health·Digest·Egress 증거 | 신산님·어울1 | External blocked |
| RSK-004 | IdP·Daon·검색 Provider 자격 미확보 | 높음/높음 | Contract Test만 있고 실제 E2E 불가 | Adapter 계약 선행, Sandbox/Credential별 차단 표시, Mock 성공 금지 | 신산님 | External blocked |
| RSK-005 | Vision/LLM-first 의미 이해가 비용·지연을 초과 | 중간/높음 | p95·비용 한도 초과 | 단일 PDF CP3, Routing/Fallback 종료상태, 비용 차단·사용자 안내 | 어울1 | Open |
| RSK-006 | Parser/OCR가 의미 이해를 대체하는 회귀 | 중간/높음 | Vision/LLM 없이 Source ready | INV-14·15 자동·E2E, UnderstandingResult와 ExtractionEvidence 분리 | 어울2·테스터 | Controlled |
| RSK-007 | Browser 코드에 내부/localhost URL 노출 | 중간/높음 | 운영 Docker에서 Client 호출 실패 | same-origin BFF 정적 검사+Network 캡처, CP3/TP 전수 확인 | 어울2·테스터 | Controlled |
| RSK-008 | Local-private 자료가 무승인 외부 전송 | 낮음/매우 높음 | EgressDecision 없는 Network | 기본 차단, 패킷 캡처, 승인 Sync만 허용 | 어울1·테스터 | Controlled |
| RSK-009 | 신규 저장소의 대량 Untracked 파일 오염 | 높음/중간 | 무관 임시·렌더 파일 Commit | `.gitignore`, 파일별 Stage 검토, 기준 Manifest, broad add 금지 | 어울1 | Mitigating |
| RSK-010 | 문서·DOCX·계획 Hash 드리프트 | 중간/높음 | Baseline 대조 실패 | Markdown 정본, DOCX 재생성·전 페이지 렌더, Manifest Hash Gate | 어울1 | Mitigating |
| RSK-011 | 개발 중단 시 상태 유실 | 중간/높음 | 작업보고만 있고 중간 상태 없음 | Work Order별 진행 파일, 단계 종료·오류·복구 즉시 기록 | 어울2 | Controlled |
| RSK-012 | 3회 실패 규칙이 예기치 않은 중단과 혼동 | 중간/중간 | 실패 횟수 과대 계산 | 원보고 유형·issue_id·Attempt Ledger 분리, 3회 시 사용자 결정 | 어울1 | Controlled |

## 위험 수용 원칙

외부 차단 위험은 숨기거나 범위에서 자동 제외하지 않는다. 관련 Work Order를 차단한 채 선행 독립 작업만 진행하며, 범위 제외·중요 위험 수용·외부 배포는 신산님 승인 없이는 실행하지 않는다.


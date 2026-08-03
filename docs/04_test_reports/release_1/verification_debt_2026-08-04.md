# Release 1 검증 부채 추적표

| 항목 | 상태 | 조치 | 완료 조건 |
| --- | --- | --- | --- |
| CP3 실제 Web Thin Vertical E2E | `GO_TO_EXECUTION` | Upstage `solar-pro3` 기반 단일 PDF 실행 환경 준비·실행 | 실제 Process·저장소·모델·Browser Network/Console 증거 |
| M5 Evidence Manifest | `PENDING` | Work Order별 증거와 checksum 소급 수집 | `docs/03_evidence/release_1/<WO>/manifest.json` |
| M6 Evidence Manifest | `PENDING` | CP3 Core·확장 Work Order 증거 소급 수집 | 동일 형식, 실제/계약 증거 구분 |
| M7 Evidence Manifest | `PENDING` | Source→질문·근거 여정 증거 소급 수집 | 실제 파일·Client E2E 증거 |
| M8 Evidence Manifest | `PENDING` | Studio 산출물·Review·Approval·Delivery·KnowledgeRegistration 증거 소급 수집 | 5종 파일 Open/Layout 및 계보 |
| M5~M7 Milestone Exit | `PENDING` | 어울1 1차 Exit 검증 수행 | 검증보고서·미해결 위험·판정 기록 |
| 내부 계약 완료 Work Order | `CONTRACT_COMPLETE / JOURNEY_UNVERIFIED` | 실제 여정 검증 전까지 제품 완료와 분리 추적 | CP3·TP 웨이브 증거 후 `VERIFYING` 전환 |

## 운영 규칙

- 이 추적표의 `PENDING` 항목이 남아 있는 동안 TP-2A·TP-2·TP-3은 제품 통과로 판정하지 않는다.
- 정적 검사·Build·Fixture 검증과 실제 Browser·Network·DB·Model 증거를 별도 항목으로 저장한다.
- 기존 제품 코드·DB·운영 서버를 변경하지 않고 증거와 검증 문서부터 복구한다.

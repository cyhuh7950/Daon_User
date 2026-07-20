# Release 1 테스트 웨이브 TP-0 보고서

## 보고 정보

| 항목 | 값 |
| --- | --- |
| 웨이브 | TP-0 문서 검증 |
| 보고일 | 2026-07-20 |
| 검토 | 기존 CLAUDE 독립 검토 원문 + 어울1 승인 반영 정합 점검 |
| 승인 | 신산님 · `APR-TP0-20260720-01` |
| 코드/Build | 없음 · TP-0 Baseline 전 문서 전용 예외 |
| 판정 | `PASS` · G0-BASELINE 준비 진행 가능 |

## 기준 문서와 Hash

| 문서 | 버전 | SHA-256 |
| --- | --- | --- |
| 상세 설계 Markdown | 승인 개정판 | `7FC4BCE7B517E915520F587D812A241E59F6C8B492671B6C8A4BC53140393C31` |
| 상세 설계 DOCX | 승인 배포본 | `C98137EC3EE007DC124F373A46F24463FCFBC4A6603F1C657E8019675D1BE53F` |
| Release 1 작업계획 | 0.6 | `80AC2EFE531895C0DA5FC777EB586A52E389E5C1A69F9CE08C931FC4F6943682` |
| Release 1 테스트 계획 | 0.7 | `C45DAE31FD408AF0D8885E006E570CC3BE36852A9F925811F8BC329C85ED9D13` |

## 시나리오 정합

| 파일 | 버전 | SHA-256 |
| --- | --- | --- |
| `00_overview.md` | 0.5 | `8718158454A9025B6CC36399AB9E0A6D456936A5539690816D729C59F8A4BC9F` |
| `01_knowledge_authority_ruleset.md` | 0.5 | `BDDB3D50CDE4E7BAC03DE868C38798A58558E16E12F7968E4EA2EDD659103898` |
| `02_model_routing.md` | 0.5 | `71D4A44FC962D6BF3C3A170064A4209140A3E7AF8DC50DE21BEF96273823E817` |
| `03_source_evidence.md` | 0.5 | `815D0DDFAB3ABBF86BEAE8DC0250571FC1E9651375AD77E56519D02C39157150` |
| `04_studio.md` | 0.5 | `197F598B24F5CEE1B08BF1457D2417F2AE4D7DBF69EBD5EEF969028FAE60BC14` |
| `05_account_security.md` | 0.5 | `8283356104039659386C6DE4F28EF6758C4BBB7593E77FFD5C279113875DE9EF` |
| `06_operations_recovery.md` | 0.5 | `6722D2EF10D01156B8DD3AED3AEE3F797E011EC3CA05811F79C2AADE6A435740` |

## 검증 결과

| 검증 | 결과 | 근거 |
| --- | --- | --- |
| Q1~Q3·Q7~Q9·N1~N3 해소 | PASS | 설계 §7.3~7.4, §8.2, §10.5, §13.2, §14.4~14.5, §18.3 |
| Vision/LLM-first·Parser/OCR 보조 | PASS | INV-14·15와 Source/모델 시나리오 |
| CP1~CP5·RC ↔ TP 7개 웨이브 | PASS | 테스트 계획 §1.4~1.5 |
| CP3 단일 PDF 초기 E2E | PASS(계약) | TP-2A와 R1-M6-10 매핑; 실행은 해당 시점에 별도 검증 |
| 시나리오 버전 | PASS | 00~06 전부 0.5 |
| DOCX 시각 검증 | PASS | 47쪽 전 페이지 렌더 확인, 잘림·겹침·깨진 표 0건 |
| DOCX 접근성 점검 | PASS WITH NOTE | High 0, Low 0, Medium 11은 코드/Callout 구현용 1-cell Layout Table의 Header 경고 |
| 외부 절대경로 구현 의존 | PASS | 저장소 상대경로 정본·Digest 계약, 개인 경로는 런타임 입력에서 배제 |

## 잔여 조건

TP-0의 문서 결함은 종결되었다. 다만 R1-D001·D003·D009·D010은 G0 제품/운영 승인 대상이고 R1-D004·D006~D008·D011·D012는 외부 환경 차단이다. 따라서 본 PASS는 개발 착수 승인이 아니며 G0-BASELINE 결정 전 구현은 `BLOCKED`다.

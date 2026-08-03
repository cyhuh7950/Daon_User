# R1-M8-02 작업지시서 — 근거 기반 보고서 DOCX·PDF

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-02` |
| Issue ID | `R1-M8-02-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §13.3, §16, §18.3 |
| 계획 근거 | Release 1 계획 R1-M8-02 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-02_progress.md` |

## 목적

확정된 GenerationSettingsSnapshot으로 근거 기반 보고서의 요약·본문·결론·인용·경고·미확인 상태와 DOCX/PDF 출력 계보를 만든다.

## 계약

- DOCX·PDF 형식만 이 작업에서 허용한다.
- Citation이 없는 결론은 `unverified`로 표시하고 근거 있는 내용과 구분한다.
- 출력 결과에는 Request·Snapshot·SourceVersion·Model 계보를 남긴다.
- 실제 파일 Open·Layout·Office/PDF 렌더 검증은 별도 증거로 남긴다.

## 허용 변경 파일

- `services/api/src/daon_user_api/report_generation.py`
- `services/api/tests/test_report_generation.py`
- 본 Work Order 진행·결과 문서

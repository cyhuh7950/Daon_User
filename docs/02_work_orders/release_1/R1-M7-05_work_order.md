# R1-M7-05 작업지시서 — iOS Capture·질문·근거

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M7-05` |
| Issue ID | `R1-M7-05-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §4.2, §8.2, §18.1, §22.1 |
| 계획 근거 | Release 1 계획 R1-M7-05 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M7-05_progress.md` |

## 목적

iOS Capture 자료를 질문·근거 계보에 연결하고 권한·ASR·Offline/Reconnect 상태를 보존한다.

## 계약

- Capture 종류는 `file`, `photo`, `audio`다.
- 권한 없이는 Capture를 저장하지 않고 `PERMISSION_REQUIRED`다.
- 음성은 ASR/LLM과 Time Segment가 있어야 `captured`다.
- Offline Capture는 보류 상태로 보존하고 Reconnect 후 Sync 대상이 된다.
- 실제 macOS Archive·Signing·iOS Device는 후속 통합 Gate다.

## 허용 변경 파일

- `services/api/src/daon_user_api/ios_capture.py`
- `services/api/tests/test_ios_capture.py`
- 본 Work Order 진행·결과 문서

# R1-M7-04 작업지시서 — Android Capture·질문·근거

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M7-04` |
| Issue ID | `R1-M7-04-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §4.2, §8.2, §18.1 |
| 계획 근거 | Release 1 계획 R1-M7-04 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M7-04_progress.md` |

## 목적

Android Capture 자료를 Source로 등록하고 질문·근거 계보를 연결하며 카메라·마이크·파일 권한을 명시적으로 관리한다.

## 계약

- Capture 종류는 `file`, `photo`, `audio`로 구분한다.
- 권한이 없으면 `PERMISSION_REQUIRED`로 중단하고 원문을 등록하지 않는다.
- Audio는 ASR/LLM 계보와 Time Segment가 없으면 `AUDIO_NOT_READY`다.
- 결과에는 Source ID·Version·Citation 계보를 보존한다.
- 실제 APK·실기기·Background·Notification은 후속 통합 검증이다.

## 허용 변경 파일

- `services/api/src/daon_user_api/android_capture.py`
- `services/api/tests/test_android_capture.py`
- 본 Work Order 진행·결과 문서

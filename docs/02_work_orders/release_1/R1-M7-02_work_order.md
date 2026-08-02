# R1-M7-02 작업지시서 — Windows Local-private Offline 지식 대화

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M7-02` |
| Issue ID | `R1-M7-02-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 · CP3 선행 미실증 |
| 설계 근거 | 상세 설계서 §4.2, §7.1, §10.4, §11.4 |
| 계획 근거 | Release 1 계획 R1-M7-02 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M7-02_progress.md` |

## 목적

Windows Local-private Workspace가 네트워크 없이 Local Model과 Local Source만으로 검색·질문·근거 확인을 수행하도록 한다.

## 계약

- Local-private Source와 `local` Model만 허용한다.
- 인터넷·Daon·External Provider·Cloud Egress는 차단한다.
- 오프라인 상태에서도 SourceVersion·Page/Cell/Region 근거 계보를 보존한다.
- Local Model이 없으면 `LOCAL_MODEL_UNAVAILABLE`로 fail-closed한다.
- 실제 EXE·Windows IPC·Local ASR·네트워크 차단은 별도 통합 검증이다.

## 허용 변경 파일

- `services/api/src/daon_user_api/local_conversation.py`
- `services/api/tests/test_local_conversation.py`
- 본 Work Order 진행·결과 문서

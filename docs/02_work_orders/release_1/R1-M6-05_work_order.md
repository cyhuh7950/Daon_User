# R1-M6-05 작업지시서 — Source 등록·보안 검사

## 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-05` |
| Issue ID | `R1-M6-05-I001` |
| 버전 | 1.0 |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 기준 저장소 | `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User` |
| 설계 근거 | 상세 설계서 §8.1, §8.2, §18.1, §18.2 |
| 계획 근거 | Release 1 계획의 R1-M6-05 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-05_progress.md` |

## 목적

사용자 파일과 직접 입력 자료가 안전한 Source가 되기 전 MIME/실형식·손상·암호화·악성·압축폭탄·민감 Injection 검사를 통과하고, 원본 digest와 불변 버전을 보존하도록 한다.

## 포함 범위

- 승인 형식 Matrix: PDF, DOCX, PPTX, XLSX, CSV, TXT, Markdown, 주요 이미지, M4A/WAV/MP3
- 확장자·선언 MIME·매직바이트 기반 실형식 일치 검사
- 빈 파일·손상·암호화·압축폭탄·기본 악성 시그니처 차단
- 원본 SHA-256 digest 보존
- 직접 입력 Source의 version 증가·편집·재색인 상태 계약
- 민감정보/Prompt Injection 의심 표식 감지 결과 보존

## 제외 범위

- 실제 AV 엔진·샌드박스·Object Storage 연동
- Parser/OCR/Vision/LLM 의미 이해 구현
- 공개 HTTP API·브라우저 화면·외부 배포

## 구현 계약

1. 선언 MIME과 실형식이 불일치하면 `MIME_MISMATCH`로 거부한다.
2. 지원하지 않는 확장자·손상·암호화·압축폭탄·악성 표식은 각각 안정적인 거부 사유를 반환한다.
3. 허용된 Source만 `accepted`가 되며 원본 digest를 결과에 포함한다.
4. 직접 입력은 최초 `version=1`, 편집 시 이전 버전을 보존하고 새 version을 만들며 재색인은 새 version에만 연결한다.
5. 민감정보·Injection 의심은 원문을 로그에 남기지 않고 플래그와 사유만 반환한다.
6. 기존 파일·DB·공개 API를 변경하지 않고 내부 Python 계약으로 제한한다.

## 허용 변경 파일

- `services/api/src/daon_user_api/source_ingest.py`
- `services/api/tests/test_source_ingest.py`
- 본 Work Order 진행·결과 문서

## 테스트 및 완료 증거

- TDD RED→구현→GREEN
- 허용 형식, MIME 불일치, 암호화·손상·압축폭탄·악성·Injection 차단, 직접 입력 version/reindex 테스트
- API 전체 unittest 회귀
- 외부 주소·비밀값 로그·브라우저 직접 호출 추가 0건

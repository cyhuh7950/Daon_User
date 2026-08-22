# R1-NOTEBOOKLM-PARITY-I001 진행 기록

## 현재 상태

- 단계: 요구사항 반영 설계·작업계획 작성
- 상태: `AWAITING_APPROVAL`
- 구현: 시작하지 않음
- 배포: 수행하지 않음
- 기록 시각: 2026-08-22

## 확정 요구사항

- 운영형 NotebookLM 동등 기능을 목표로 한다.
- 현재 Workspace와 Studio 카드 배치는 유지한다.
- 일반 Source는 PDF로 제한하지 않는다.
- MCP와 Daon 승인 지식을 연결형 Source로 추가한다.
- Source 등록·삭제는 사용자가 즉시 수행한다.
- Source 등록 시 `Notebook과 함께 삭제`와 `Notebook 삭제와 무관하게 보관`을 선택한다.
- 동일 파일 내용은 Digest로 중복 저장하지 않는다.
- 외부 원본 소실은 자동 삭제하지 않고 `사용 불가`로 표시한다.
- 대화와 Studio는 선택된 Source를 사용하고, 일반 업무 상담도 허용한다.

## 생성 문서

- 상세 설계안: `docs/superpowers/specs/2026-08-22-notebooklm-parity-design.md`
- 작업계획: `docs/superpowers/plans/2026-08-22-notebooklm-parity-implementation-plan.md`

## 다음 단계

신산님이 설계서와 작업계획서를 승인하면, 승인된 계획의 Task 1부터 Subagent 작업을 시작한다. 승인 전에는 코드 수정·테스트·배포를 하지 않는다.

## Task 1 실행 기록 (R1-NOTEBOOKLM-PARITY-I001-T1)

- 착수: 2026-08-22, 공식 작업공간 `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, branch `codex/user-auth-screen-split`, HEAD `3342d6c88bcb5191322bd1d1e179b5e6c41d3760`; 기존 dirty 변경은 보존.
- 단계 완료: Source 계약 확장. `SourceRecord`에 Notebook 귀속·content type·삭제 정책·unavailable 상태를 추가했고, 파일 형식은 기존 MIME Matrix를 활용하도록 유지했다. 외부 원본 소실을 자동 삭제하지 않고 `unavailable`로 표시하는 변환을 추가했다.
- 단계 완료: 업로드 서비스에 일반 Source 계약(`register_source`)과 기존 `register_pdf` 호환 래퍼를 추가하고, content type·삭제 정책을 Canonical payload로 전달하도록 확장했다. 동일 digest 조회 시 기존 object/source version을 재사용하는 경로를 추가했다.
- 테스트: `services/api`에서 `$env:PYTHONPATH='src'; uv run pytest tests/test_source_lifecycle_contract.py tests/test_source_ingest.py tests/test_source_upload.py -q` → `9 passed, 4 subtests passed`.
- 오류/복구: 저장소 루트 및 API 디렉터리에서 PYTHONPATH 없이 실행한 첫 두 테스트는 `ModuleNotFoundError`로 수집 실패. 패키지 구조에 맞게 `PYTHONPATH=src`로 재실행해 통과.
- 미해결: 실제 HTTP 업로드 경계는 허용 범위 밖 `runtime.py`에서 여전히 `application/pdf`를 강제하므로, 비-PDF 엔드포인트 통합은 상위 에이전트 판단이 필요하다. DB에 삭제 정책 전용 컬럼을 추가하지 않고 canonical JSON payload로 전달했으므로 운영 DB 회귀 검증도 필요하다.
- 다음: 상위 에이전트가 runtime 경계 수정 허용 여부와 DB 계약/마이그레이션 범위를 판단한 뒤 통합 테스트를 진행한다.

### Task 1 재개 승인 및 추가 검증

- 승인: 2026-08-22, 상위 에이전트가 `runtime.py` HTTP 업로드 경계 확장과 DB 계약 판단을 승인하여 Task 1을 재개.
- 변경: `runtime.py`가 파일명 확장자와 MIME Matrix를 함께 검증하도록 변경. PDF 호환을 유지하면서 txt/md/csv/docx/pptx/xlsx/png/jpg/jpeg/m4a/wav/mp3 업로드를 SourceIngestor 계약으로 전달한다. 잘못된 MIME은 415, 잘못된 파일 시그니처는 Source 계약 오류로 거부한다.
- 변경: 업로드 서비스에 요청 content type을 전달하고 canonical JSON에 삭제 정책·content type을 기록한다. 스키마를 확인한 결과 `source_versions.canonical_json`이 기존 정식 불변 payload 저장 지점이므로 별도 DB 컬럼/migration은 만들지 않았다.
- 테스트: `$env:PYTHONPATH='src'; uv run pytest tests/test_source_lifecycle_contract.py tests/test_source_ingest.py tests/test_source_upload.py tests/test_source_upload_runtime.py -q` → `15 passed, 6 warnings, 4 subtests passed`.
- 미실행: 브라우저 E2E, ysna-server 배포·DB/MinIO 통합 검증은 이 Task에서 실행하지 않음.

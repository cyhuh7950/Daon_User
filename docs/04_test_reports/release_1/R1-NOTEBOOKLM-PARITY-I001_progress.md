# R1-NOTEBOOKLM-PARITY-I001 진행 기록

## 현재 상태

- 단계: Task 1 구현·서버 배포 검증
- 상태: `INTEGRATION_CHECK_PENDING`
- 구현: 완료 (`f078085`)
- 배포: ysna-server에 `f07808563119a549ca583076714f286063bd171b` 배포 완료
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

Task 1 구현·빌드·서버 기동은 완료했다. 다음은 로그인 세션이 필요한 실제 브라우저 Source 등록/삭제와 DB·MinIO 통합 검증이다.

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

### ysna-server 배포 확인

- 배포: `f07808563119a549ca583076714f286063bd171b` 기준 API·document-worker·web 이미지를 재빌드하고 재기동했다.
- 빌드: API/worker 이미지 빌드 성공. Web Next.js 빌드·TypeScript·`verify-product-ui-boundary` 성공(`violations: []`).
- 런타임: API `healthy`, Web `healthy`, document-worker 실행 중, 기존 MinIO `healthy`.
- 공개 확인: `https://daon-user.sinsan.kr/notebooks` → HTTP 200.
- 프로필 확인: API 컨테이너 `DAON_RUNTIME_PROFILE=production`; `DAON_DEV_AUTH_BYPASS`는 설정되지 않음.
- 미완료: 로그인된 브라우저에서 실제 PDF/비-PDF 등록·처리 완료·삭제와 DB/MinIO 실물 정합성은 아직 확인하지 못했다. 이 검증 전에는 Task 1을 최종 완료로 판정하지 않는다.

### Task 2 UI Source 추가 구현 (R1-NOTEBOOKLM-PARITY-I001-T2)

- 착수/환경: 2026-08-22, 공식 작업공간 `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, branch `codex/user-auth-screen-split`; 기존 dirty 변경은 보존하고 새 branch는 생성하지 않음.
- 단계 완료: 기존 3면 배치를 유지하면서 Source 추가를 모달 흐름으로 확장했다. 파일 업로드(일반 MIME), 웹사이트, Drive, 복사한 텍스트 탭을 제공하고, 아직 Connector 계약이 없는 웹사이트·Drive는 mock/API 임의 주소 없이 `연결 준비 필요`로 표시한다.
- 단계 완료: 업로드 클라이언트와 Notebook adapter를 generic `uploadSource` 계약으로 연결했다. 브라우저 요청은 기존 same-origin `/bff/api/workspaces/{workspaceId}/sources` 경로를 유지하고, notebook id는 adapter에서 전달한다. `uploadPdf` 호환 래퍼는 기존 호출부 회귀를 위해 유지했다.
- 단계 완료: 등록 중 상태, 안전한 오류 코드, `unavailable` Source 상태 라벨을 UI에 반영했다. 붙여넣은 텍스트는 text Source로 등록 요청하며 서버 계약을 우회하지 않는다.
- 변경 파일: `packages/ui/src/product-workspace-shell.jsx`, `packages/ui/src/notebook-context-adapter.js`, `packages/ui/src/workspace.css`, `apps/web/lib/source-upload-api.js`, `apps/web/components/actual-workspace.jsx`, `apps/web/lib/product-workspace-api.js`, `scripts/tests/notebook-source-add-flow.test.mjs`.
- 오류/복구: 신규 테스트의 Unicode 정규식 문법 오류를 문자열 assertion으로 수정. 기존 Studio 오류 테스트가 자동 retry 대기 중 상태를 검사하던 문제를 확인하고 계약/list 실패는 즉시 UI 오류로 표시하며 일시적 transport 오류만 자동 retry하도록 분리했다.
- 테스트: `node --test scripts/tests/notebook-source-add-flow.test.mjs scripts/tests/product-workspace.test.mjs` → `22 passed`. `git diff --check` → 공백 오류 없음(기존 dirty 파일의 줄바꿈 경고만 존재).
- 미해결: 웹사이트·Drive 실제 Connector와 로그인 브라우저 E2E, ysna-server 재배포 및 DB/MinIO 실물 등록 검증은 상위 통합 단계에서 수행해야 한다. 이번 Task에서는 임의 mock을 넣지 않았다.

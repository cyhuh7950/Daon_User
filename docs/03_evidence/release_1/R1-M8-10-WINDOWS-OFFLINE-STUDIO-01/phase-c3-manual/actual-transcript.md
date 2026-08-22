# Phase C 메뉴 3 사용자 설명서 Actual Transcript

- issue: `R1-M8-10-WINDOWS-OFFLINE-STUDIO-01-I001 / PHASE_C_MENU_3_MANUAL`
- checkout: `codex/user-auth-screen-split` @ `2d4c59e1c761ec12848dcfac8c2f04078dcbb47b`
- 실행일: 2026-08-16 (Asia/Seoul)
- 배포·로그인 연결·Notebook Domain 변경: 0
- Credential·Token·내부 운영 주소·문서 원문 로그: 0

## TDD

1. RED: Markdown 정본 3종, Release/Web manifest, same-origin client, Manual Hub UI 부재로 `0/4`.
2. GREEN: 정본·client·adapter·UI·asset manifest 구현 후 focused `5/5 PASS`.
3. Client negative: unknown document, traversal/절대 href, unknown Release, MIME/bytes/SHA mismatch를 fail-close.

## 문서 산출물

- workspace dependency bundle `26.813.12317` 사용.
- `compact_reference_guide` exact token: Letter, 1 inch margins, 9360 DXA usable width, Calibri 11, H1 16/H2 13/H3 12, list 0.187/0.375/0.188 inch, table 9360 DXA와 cell margin 80/80/120/120.
- `editorial_cover` 패턴과 동일한 guide header를 3종에 일관 적용.
- DOCX expected3/create marker 성공 1회, PDF expected3/create marker 성공 1회. 잘못된 package 위치와 DOCX-only marker의 PDF 요청은 사용법 검증 단계에서 거부되어 marker가 생성되지 않았고, artifact authoring은 각 전용 marker 성공 이후에 시작함.
- DOCX: 3종 × 6페이지 = 18페이지 `render_docx.py` 전수 렌더·시각 점검.
- PDF: 3종 × 6페이지 = 18페이지 bundled `pdftoppm` 전수 렌더·시각 점검.
- 수정 반복: Markdown inline marker 노출 제거, 실제 restart numbering, list suffix/indent 겹침 제거, table header 접근성 지정.
- 최종 a11y audit: 3종 모두 high=0, medium=0, low=0.
- 최종 점검: 정적 목차 내부 링크, real Heading/numbering, 표 폭·cell margin, 이미지 alt text, 한글 glyph, 머리말·꼬리말·페이지 번호, 잘림·overlap 0.

## Actual Browser 1920×1080

- `01-manual-hub-list-1920x1080.png`: Release 1.0.0, 문서 3종, Web 읽기, DOCX/PDF control 표시.
- `02-manual-hub-read-1920x1080.png`: 검색어 `지식`으로 목록 1건, 지식·LLM 가이드 Web 본문 읽기.
- `03-manual-download-1920x1080.png`: Getting Started DOCX/PDF 실제 fetch 완료.
- Browser viewport는 CDP `1920×1080`, deviceScaleFactor 1로 확인.
- Network JSONL은 실제 요청 11행이며 unique path는 `/manual/manifest.json`, `/manual/daon-knowledge-llm-guide.md`, `/manual/daon-getting-started.docx`, `/manual/daon-getting-started.pdf` 4개다. 모두 same-origin 상대 경로만 사용한다.
- Capture Harness는 test-only이며 제품 Web package/build entry에 포함되지 않음.
- Capture 종료 후 owned Chrome/Vite PID 및 4187/9347 listener 0.

## 자동 검증

- focused Manual: `5/5 PASS`.
- product lint: `3 files PASS`.
- Product UI boundary: `300 files, violations 0, boundary errors 0`.
- Web production build/TypeScript/static generation: PASS.
- Desktop Vite build: PASS.
- 관련 회귀: `25/26 PASS`; 실패 1건은 Phase C2에 추가된 Desktop License adapter 2 method를 기존 exact-7 기대값이 반영하지 못한 선행 stale test.
- broad Workspace: `36/37 PASS`; 실패 1건은 선행 Studio state 확장 필드를 기존 exact DTO 기대값이 반영하지 못한 stale test.
- Manual 변경이 위 두 assertion의 actual/expected 차이에 관여하지 않음을 focused test와 diff로 분리함.

## 판정

Phase C 메뉴 3 범위는 GREEN. 준비 중인 Notebook 홈·생성 및 로그인 최종 연결은 설명서에서 완료 기능으로 기술하지 않았으며 후속 Phase로 명시했다.

## 독립 리뷰 보완

- RED: 형식상 정상인 미승인 Release `9.9.9`와 rogue document ID가 기존 schema 검사만 통과했으며, Evidence Network 요청 수가 unique path 수 `4`로 hardcode되어 실제 JSONL 11행과 달랐다.
- GREEN: Browser client는 승인 Release `1.0.0`, 정본 document ID 3종 exact set, document version=Release를 모두 요구한다. 미승인 Release·rogue ID·혼합 version은 `MANUAL_MANIFEST_INVALID`로 fail-close한다.
- Evidence builder는 `browser-network.jsonl`을 직접 parse해 `captured_requests=11`, `unique_request_paths=4`를 별도 계산한다.

## 2차 독립 리뷰 보완

- RED: `readManualDocument`와 `downloadManualAsset`에 caller가 직접 전달한 duck manifest는 strict projection을 우회해 악성 href·shape에서도 fetch가 1회 발생하고 `MANUAL_CONTENT_INVALID`로 뒤늦게 실패했다.
- GREEN: 내부 `fetchAsset`은 caller manifest도 항상 pure `projectManifest()`로 재검증한다. absolute/traversal href, rogue ID/version, MIME/SHA/shape 변조는 `MANUAL_MANIFEST_INVALID`, fetch count0으로 차단한다.
- 정상 projected flow의 same-origin, bytes/SHA/MIME 검증은 유지하며 focused Manual `8/8 PASS`다.

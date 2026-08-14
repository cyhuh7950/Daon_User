# Foundation B3 Knowledge Context actual Gate

- 실행 시각: 2026-08-15T04:04:41+09:00
- Checkout: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`
- Branch/HEAD: `codex/user-auth-screen-split` / `dbe67f9bfe778b1ffa10b31f1e3e0faf807dd42b`
- 범위: Raw Source와 Daon 생성 지식을 같은 Evidence Resource 계약으로 질문·Citation에 결속

## Actual PostgreSQL 15 Gate

- disposable DB: `daon_b3_20260815_knowledge_context_it`
- migration: `0001 -> 0016` PASS
- 제품 repository 실제 실행:
  - 승인 Studio Output 본문을 Knowledge Package로 등록
  - generated Source, SourceVersion, EvidenceSpan, Index 행 생성
  - 생성 지식 본문 보존 확인
  - Citation 조회가 `text/plain; charset=utf-8`와 `section` locator를 반환
  - 기존 RLS, FK, immutable, concurrency 계약 유지
- 결과: `1 passed`
- cleanup: disposable DB 0, disposable role 0, 기존 `local-postgres` running 유지

## Actual Browser same-origin Gate

- 격리 API: `127.0.0.1:18483`
- 격리 Web: `127.0.0.1:14183`
- Browser가 호출한 공개 경로: `/bff/api/*` only
- 화면 상태:
  - Daon 승인 지식 1건과 Raw Source 1건 동시 선택
  - 질문 Context: `Daon 승인 지식 + Raw Source`
  - 질문 실행 1회, HTTP 200
  - 요청 body resources:
    - `knowledge_package / knowledge-package-daon3`
    - `source / source-raw / version-raw`
  - 답변 Citation:
    - Daon 생성 지식: `/bff/api/workspaces/workspace-b3/citations/citation-daon/content` (`section`, PDF fragment 없음)
    - Raw Source: `/bff/api/workspaces/workspace-b3/citations/citation-raw/content#page=2` (`page`)
  - 내부 API 주소, credential, stack trace 노출 0
  - rebuilt BFF Citation response: HTTP 200, `content-type: text/plain; charset=utf-8`, `x-citation-locator-kind: section`

## 자동 검증

- Python B3 focused: `38 passed, 1 skipped, 3 subtests passed`
- Python API full: `393 passed, 28 skipped, 137 subtests passed`
- Node BFF/OpenAPI/Product/Question: `70 passed`
- BFF typed Citation header TDD: RED `null !== section` -> GREEN `1 passed`
- OpenAPI Citation locator TDD: RED enum absent -> GREEN `1 passed`
- Web production build/TypeScript: PASS
- Web boundary: `269 files / violations 0`
- OpenAPI R1-M8 evidence: `paths 79 / operations 98 / schemas 127 / errors 31`, SHA-256 `B859F3CB36100C0D01896B269C17A6BBEA55C957391BFB5F6662D5E75CC3BD62`

## Evidence

- Browser screenshot: `foundation-b3-knowledge-context-browser.png`
- Screenshot SHA-256: `738091F03B66D44BD727B52AE6712FB18E12951C28DB7607705753C0912615AB`
- Browser fixture source SHA-256: `57685A9705E990F06D2462F5EF2814EBDB0CF4DADAEB37AF2CCCCC4077147AA2`

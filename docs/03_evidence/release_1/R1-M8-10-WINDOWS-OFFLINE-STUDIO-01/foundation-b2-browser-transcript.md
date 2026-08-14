# Foundation B2 Source·지식·권위 actual Browser Gate

- 실행 시각: `2026-08-15T01:02:10+09:00`
- 범위: current-source Web `14184` → same-origin `/bff/api/*` → disposable Runtime `18484`
- 저장소: disposable SQLite Identity/Authorization + Runtime Knowledge/Source projection fixture
- 외부 Provider 전송: `0`

## 실제 사용자 흐름

1. disposable local user로 로그인해 Workspace에 진입했다.
2. `Source·지식·권위` Pane에서 `Daon 승인 지식` 1건과 `Raw Source` 2건을 확인했다.
3. 승인 지식 `Daon 2.5 · 2.5.7`을 실제 클릭했고 `aria-pressed=true`를 확인했다.
4. ready Source는 `사용 가능`, `needs_review` Source는 `검토 필요`로 서로 다르게 표시됐다.
5. ready Source만 선택·질문 대상이고 검토 필요 Source는 비활성 상태를 유지했다.
6. Digest, Knowledge Registration ID, Output Version ID, Trace, 내부 URL, SQLSTATE, Stack 원문은 DOM에 나타나지 않았다.

## actual downstream HTTP

- `POST /api/v1/auth/login` → `200`
- `GET /api/v1/workspaces/{workspace_id}/sources` → `200`
- `GET /api/v1/workspaces/{workspace_id}/knowledge-packages` → `200`
- `GET /api/v1/studio-outputs?workspace_id={workspace_id}` → `200`

Browser는 same-origin `/bff/api/*`만 사용하고 Runtime access log에는 downstream `/api/v1/*` 요청만 기록됐다.

## 화면 증거

- 파일: `foundation-b2-source-knowledge-browser.png`
- bytes: `79897`
- SHA-256: `9C59C1E42372A3390CA19E3B4070C19914C1169E5F8F62AACDA801C43994E4BF`
- 기준: `1920×1080`

## 검증과 정리

- Browser console warning/error: `0`
- Knowledge 선택 후 `aria-pressed=true`
- Raw Source `2`, 승인 지식 `1`
- actual Browser 종료 후 임시 API/Web listener, SQLite, log, PID 파일을 제거한다.

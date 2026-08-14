# Foundation B1 LLM 설정 actual Browser Gate

- 실행 시각: `2026-08-15T00:45:56+09:00`
- 범위: current-source Web `14183` → same-origin `/bff/api/*` → disposable Runtime `18483`
- 저장소: disposable SQLite Identity/Authorization + in-memory Provider repository
- Provider transport: 실제 외부 호출 0. 고정 safe checker로 Web/BFF/Runtime 수직 연결만 검증
- 실제 Provider 생성/품질: A5 Upstage actual Gate와 분리

## 실제 사용자 흐름

1. disposable local user로 로그인했다.
2. redirect된 Workspace에서 `설정 → LLM 설정`을 열었다.
3. 9개 Provider 카드와 선택 Provider 상세가 렌더됐다.
4. UPSTAGE Endpoint를 승인 exact URL로 입력하고 Provider를 활성화한 뒤 저장했다.
5. `연결 시험`을 실제 클릭했다.
6. 화면은 `UPSTAGE 연결을 확인했습니다.`와 `연결 확인됨`을 표시했다.
7. Endpoint 원문, Runtime 내부 주소, Trace, Credential 원문은 DOM에 나타나지 않았다.

## actual downstream HTTP

- `GET /api/v1/model-profiles?...` → `200`
- `GET /api/v1/model-deployments?...` → `200`
- `GET /api/v1/workspaces/{workspace_id}/model-policy` → `200`
- `POST /api/v1/model-profiles` → `201`
- `GET /api/v1/model-profiles/UPSTAGE/connection-check?...` → `200`

Browser 호출은 same-origin `/bff/api/*` Adapter를 통했고 API access log에는 위 downstream `/api/v1/*`만 기록됐다. Browser source의 absolute/loopback 직접 호출은 기존 boundary와 focused contract에서 0이다.

## 화면 증거

- 파일: `foundation-b1-llm-settings-browser.png`
- bytes: `67082`
- SHA-256: `F95084FB970840E3C8CCB289365CA82A9D9D1E92093960E593FFE12C6A183B18`

## 보안·정리

- 실제 Provider credential 사용 0
- disposable password는 격리 fixture에만 사용하고 Evidence/제품 State에 저장하지 않음
- 외부 Provider request 0
- 종료 후 Browser tab, API/Web listener, SQLite DB, temp logs를 제거하고 포트 잔류 0을 확인한다.

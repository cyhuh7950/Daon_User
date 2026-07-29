# Repository 독립성 검사 계약

## 목적

`npm run verify:independence`는 Daon 사용자 프로그램이 다른 Daon 제품의 Package, Source, 경로, Runtime Image, 내부 Endpoint에 직접 의존하지 않고 Repository 경계와 Browser same-origin 계약을 유지하는지 검사한다. 성공은 정적 검사의 통과이며 후속 Work Order의 실제 Browser Network 검증을 대체하지 않는다.

## 검사 범위

- `repo-boundaries.json`의 8개 구성요소와 Workspace Package 간선을 구조적으로 검사한다.
- `package.json`, `pyproject.toml`, `package-lock.json`, `repo-boundaries.json`은 일반 문자열 제외 여부와 무관하게 구조 검사 계약에 포함된다. Lockfile은 외부 Registry URL을 정상 메타데이터로 취급하되 다른 Daon 제품이나 저장소 직접 Package 의존은 허용하지 않는다.
- App, Service, Package, 일반 Script, Docker/Compose, CI 실행 파일을 검사한다.
- 문서·보고·Evidence, Build/Cache, `node_modules`, `.git`, 검사기 Policy·구현·Test 자체만 정확 경로로 일반 문자열 검사에서 제외한다. 제품 Source 예외는 없다.

## 차단 규칙

| rule_id | 차단 내용 |
| --- | --- |
| `DEP_GRAPH_BOUNDARY` | 미등록·자기·순환·허용 밖·금지 구성요소 의존 |
| `PACKAGE_DAON_INTERNAL` | Daon2/2.5/3 내부 Package 및 저장소 경로 Package 의존 |
| `SOURCE_IMPORT_BOUNDARY` | App/Service 간 Source 직접 Import와 다른 Daon Source Import |
| `PATH_EXTERNAL_ABSOLUTE` | 실행 Source·설정의 개인/외부 절대 경로와 다른 Daon 저장소 경로 |
| `RUNTIME_IMAGE_DAON` | Docker/Compose/CI의 다른 Daon Runtime/Base Image |
| `BROWSER_DIRECT_API` | Browser 후보의 절대 URL, localhost, Docker Host/Port, `NEXT_PUBLIC_API_BASE_URL` API 직접 호출 |
| `CONNECTOR_BYPASS` | `services/api/src/connectors/daon` 승인 Adapter 밖 내부 Client/SDK/Endpoint 사용 |

표시 문구인 “Daon 승인 지식”과 공개 Connector 계약 이름은 위반이 아니다. 내부 URL·SDK·DB·Source 직접 의존만 차단한다.

## Browser와 Server 분류

`apps/web`에서 `'use client'`를 선언하거나 `client`, `browser`, `components`, `hooks`, `ui` 경로에 있는 JavaScript/TypeScript 파일은 Browser 후보다. Route Handler, Server Action, `.server.*`, `api`, `bff`, `proxy`, `server` 경로는 Server 후보다. Browser 후보는 `/api/...` 같은 same-origin 상대 경로만 사용한다. Native Client는 Browser 후보에 포함하지 않는다.

## 실행과 결과

```text
npm run verify:independence
node scripts/verify-repository-independence.mjs
```

- Exit 0: 위반 0건. Graph와 위반 JSON을 `docs/03_evidence/release_1/R1-M1-04/`에 기록한다.
- Exit 1: 계약 위반. `rule_id`, 파일, 줄, Masking된 근거와 수정 경계를 출력한다.
- Exit 2: Policy Schema 오류 또는 검사 불능.

예외 변경은 `rule_id`, 정확 경로, 사유, 소유자, 만료/재검토 조건을 갖춘 승인 변경으로만 가능하다. 현재 `exceptions`는 빈 배열이며 임의 Source 예외를 추가하지 않는다.

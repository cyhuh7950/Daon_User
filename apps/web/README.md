# Web Application Boundary

## 책임

Production Web UI와 Server-side BFF 경계를 소유한다. Browser 코드는 same-origin 상대 경로만 호출하고 내부 API 주소·Provider URL·Secret을 소유하지 않는다.

## 허용 의존

- `packages/ui`
- `packages/contracts`
- `packages/design-tokens`

## 금지 의존

다른 App과 Service의 내부 Source Import를 금지한다. Cloud Service 연동은 BFF와 공개 Contract를 통해서만 수행한다.

## 후속 Build

독립 실행·Build 소유 Work Order는 `R1-M3-01`이다.

## same-origin API BFF

`app/bff/api/[...path]/route.js`는 R1-M4-05의 고정 allowlist server route다. Browser는 `/bff/api/...` 상대 경로만 사용하며 내부 API origin을 알지 못한다. `DAON_API_INTERNAL_URL`은 server process에만 설정하고 `NEXT_PUBLIC_*` 설정으로 전달하지 않는다. 검증은 저장소 루트의 `npm run verify:api-runtime`에서 실제 Next production process로 수행한다.

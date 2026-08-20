# 실제 데이터 기반 대화·Studio 검증 Transcript

- Issue: `R1-M8-10-REAL-DATA-CONVERSATION-E2E-I001`
- Date: `2026-08-20`
- Fixture inventory: product Source `0`, fixture Source `0`, delete `0` (`already absent`)

## 실제 PostgreSQL

- 환경: WSL `local-postgres`, 고유 disposable DB/Role
- Migration: fresh `0001 → 0020`
- Tests: `3 passed`, skipped `0`
- 일반 대화: Source/Object storage read `0`, Citation `0`, selected Provider attempt `1`, Run·Conversation·Notebook binding `1`
- grounded 흐름: selected Notebook Source binding, Citation `1`, Studio 저장·동일 key replay, 다른 Notebook write `0`
- HTTP authoritative replay: 동일 replay의 current binding/provider/policy/ask `0`, fingerprint mismatch write `0`, cross-notebook replay `0`
- Cleanup: disposable DB `0`, Role `0`

## 대표 Provider transport compatibility

- 기존 서버 설정 boolean: `UPSTAGE`, `GROQ`, `MISTRAL` configured
- 선택: `UPSTAGE`
- bounded call: `1`
- 결과: HTTP `200`, response schema `valid`, Citation `0`, secret echo `0`
- Key·응답 원문·Credential: 출력/복사/Artifact `0`
- 운영 DB write/deploy root change: `0`
- 원격 고유 temp cleanup: remaining `0`

이 검증은 기존 ysna-server Provider transport compatibility만 증명한다. 새 제품 코드는 배포하지 않았고 Credential을 로컬로 반입하지 않았으므로, 새 제품 경로의 실제 Source 업로드→처리→Provider 대화→Citation→Studio 전체 호출은 `NOT_RUN`이다.

## 계약·자동 회귀

- API focused: `36 passed`, 실제 PostgreSQL tests `3 passed` 별도
- Node related: `87/87 passed`
- Rust Native wire: `3/3 passed`
- Web production build: PASS; product UI boundary `349 files`, violations `0`
- Desktop production build: PASS
- OpenAPI: `75 paths`, `94 operations`, `120 schemas`, `31 errors`; SHA-256 `594AED28565CCDBA60F3A12565071F7EAE5239544D5632508BD612EA8D180E0A`
- Fixture production import graph: violations `0`
- same-origin BFF 일반대화 exact body: PASS
- UI intent provenance: general label PASS, grounded no-citation label `0`, stale response answer/label `0`
- Unicode intent exact vector: Python/JS/Rust 모두 fullwidth letter·`！`·`？`·U+3000 space fail-close, 정상 ASCII `!`·`?` 허용
- 최종 Minor focused: Python `1/1`, Node `1/1`, Rust `1/1` PASS. actual Provider/PostgreSQL/Windows 재실행 `0`

## 실제 화면 판정

- React actual behavior test: 빈 Notebook에서 일반대화 실행·`일반 대화 · 근거 미사용`, grounded 입력 차단 PASS
- Browser 1920×1080 새 제품 실제 Provider 수직 흐름: `NOT_RUN`
- Windows actual 새 제품 실제 Provider 수직 흐름: `NOT_RUN`
- 따라서 overall은 `PARTIAL`; 자동 계약 PASS를 actual 제품 E2E PASS로 과장하지 않는다.

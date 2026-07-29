# R1-M4-06 완료보고

## 판정

**COMPLETED — Loopback Local API를 단기 요청 Token·Process/Instance 결합·고정 Read-only Command registry·Browser/Proxy/Host/Target 차단으로 fail-close하고 실제 Packaged Process 검증을 완료했다.**

## 판단 이유

- Bootstrap의 256-bit root secret을 HTTP Token으로 직접 사용하지 않는다. Rust owner가 60초 HMAC-SHA256 요청 Token을 실행 Instance·Capability·Command·시각·nonce에 결합한다.
- 허용 Command는 `runtime.status.read`, `runtime.capabilities.read` 두 개뿐이고 Write·저장·모델·Sync Command는 등록하지 않았다.
- 정상 요청은 Token 검증 후 Capability/Command/Method/Body 순서로 dispatch하며 동일 nonce, 이전 실행 Token, 변조·미래·만료 Token을 거부한다.
- 실제 Listener는 매 실행 `127.0.0.1` 동적 Port 1건뿐이고 외부·wildcard Listener는 0건이다.
- Host·Forwarded·absolute-form·query·encoded path·Browser Origin·과대 Header·Body 우회를 실제 Packaged Sidecar에서 모두 거부했다.
- 부모 stdin EOF 후 두 실행 모두 정상 종료했고 잔여 Process와 Listener는 0건이다.
- Rust manager가 실제 Packaged Python Sidecar를 두 차례 start·retry·shutdown하는 교차언어 수명주기를 통과했다.
- 전체 Quality Gate 7개 범주가 failure 0으로 통과했고 Identity·Authorization·OpenAPI·Independence 회귀도 통과했다.

## 조치

- Branch `codex/r1-m4-06`의 단일 구현 Commit을 Push한다.
- PR·CI·Merge와 다음 Work Order 착수 판단은 어울1이 수행한다.
- 실제 GUI를 열지 않았으며 화면 검증을 주장하지 않는다. 외부 배포·DB Migration·공개 API 변경도 수행하지 않았다.

## 주요 변경

### Local Service

- `security.py`: HMAC-SHA256 Token 발급/검증 계약, TTL 상한, 상수시간 비교, thread-safe nonce replay cache
- `app.py`: versioned Capability catalog, 고정 Command registry, 정확 Host와 Browser/Proxy/Target/Header/Body 방어, 안전 오류 envelope
- `protocol.py`·`main.py`: strict Bootstrap, root secret·부모 PID 결합, Windows packaged process ancestry와 stdin EOF 생존 계약

### Rust owner

- App Instance별 CSPRNG root secret 생성과 비노출 Debug
- 실제 Rust PID를 포함한 Bootstrap
- 고정 Read-only 명령만 허용하는 60초 요청 Token 발급
- Health 요청마다 새 Token 사용, WebView에 Port·Token·내부 URL 미노출
- 기존 Windows process fixture의 부하 민감 대기만 bounded 범위에서 정합화

### 검증기

- 실제 Sidecar 2회 기동, 이전 실행 Token과 replay 차단, listener/cleanup attestation
- Node owner 요청과 Browser metadata 요청을 분리해 명시적 Browser 차단 검증
- Quality static scan이 compiler target과 Python/검사 cache 같은 생성 바이너리를 Source로 오판하지 않도록 제외 계약과 회귀 테스트 추가

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| Python unit/coverage | 51 PASS · 94.17% |
| Python lint/type/contract/build | PASS · mypy strict 10 files · contract 30 PASS |
| Python dependency audit | 알려진 취약점 0 |
| Rust owner/contract | lib 16 PASS · integration 3 PASS |
| Desktop JS/bridge/runtime verifier | 28 PASS |
| 실제 Packaged Sidecar | 2 runs PASS · loopback only · clean exit/listener close |
| Rust owner 교차언어 lifecycle | 2 runs PASS · secret output 0 |
| Identity/Authorization | 18 PASS · 22 PASS |
| OpenAPI | 44 paths · 67 operations · 53 schemas PASS |
| Independence | violation 0 |
| 최종 Quality Gate | 7 Category PASS · failure 0 |
| Browser Source/Bundle 금지 문자열 | 0 hit |

## Evidence

- `docs/03_evidence/release_1/R1-M4-06/security-contract-summary.json`
- `docs/03_evidence/release_1/R1-M4-06/packaged-runtime-summary.json`
- `docs/03_evidence/release_1/R1-M4-06/validation-summary.json`
- `docs/04_test_reports/release_1/R1-M4-06_progress.md`

## 제외 범위·남은 위험

- GUI Browser·실제 사용자의 Tauri 화면 클릭은 이번 Headless 보안 계약 범위가 아니며 수행하지 않았다.
- 외부 배포, ysna-server, PostgreSQL, Local 저장·Vector·Model·ASR·Sync 업무 기능은 제외했다.
- Windows PID는 단독 인증수단이 아니다. root secret, App Instance, HMAC Token, stdin EOF 소유와 결합해 사용한다.
- 최종 GitHub CI와 Merge 판정은 어울1 소유다.

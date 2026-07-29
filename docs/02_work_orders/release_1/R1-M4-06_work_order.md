# R1-M4-06 Loopback Local API 보안 작업지시서

## 승인 기준과 Writer

- Work Order: `R1-M4-06`.
- Branch `codex/r1-m4-06`, 기준 HEAD `81c113b1081cf55acaada5fe3680ee8aac429638`, 시작 Clean.
- 승인 정본: `AGENTS.md`, 상세 설계서 §15.1·§17.3·§21.3·§25.1, Release 1 작업계획 §14의 R1-M4-06, 테스트계획 §10 Local API.
- 선행 `R1-M3-03`, `R1-M4-03`, `R1-M4-04`의 병합 결과와 증거를 재사용하되 현재 Source·실행 결과로 재검증한다.
- 어울2가 이 Worktree와 범위의 유일한 Writer다. PR·CI·Merge와 완료 판정은 어울1 소유다.

## Baseline Manifest 정합 기록

- 착수 사전점검에서 승인 후 개정된 문서와 Manifest Hash 드리프트를 확인했고, 어울1이 `CHG-R1-MANIFEST-002` C1 메타데이터 정합으로 해소했다. 우회 승인이나 Hash 검사 생략이 아니다.
- 현재 canonical Git Blob SHA-256은 상세 설계 `44A5AA9CDA2381555FE4D4AB238838E404A8ACB6515236CE5C33CD0E0E60388E`, 작업계획 `6693010D25B304A134AC414CB8F884D21DC6DF1365D98D855F2732BE4840F047`, 테스트계획 `C45DAE31FD408AF0D8885E006E570CC3BE36852A9F925811F8BC329C85ED9D13`, 결정 기록 `AF15CE3B3C37A26524A61C2DB27C46DA27E6197949C710C6972D37CB3E904BAA`다.
- 기존 ysna 통합 `R1-D021`, Next 임시 보안 브리지 `R1-D022`, iOS 알림 설정 `R1-D023`으로 중복 ID를 제거했다. 제품 기능·공개 API·데이터·보안 계약은 변경하지 않았다.

## 단일 목표와 완료 정의

- 목표: Windows Tauri가 소유하는 Local Service의 Loopback API를 외부 Interface·Browser·다른 Local Process·위조 Instance·위조/만료 Token·Allowlist 밖 명령으로부터 fail-close한다.
- 기존 M3-03 골격의 `127.0.0.1` 동적 Port, Parent pipe EOF 종료, WebView 비밀 미노출, Rust Process 소유권을 보존하고 실제 보안 계약으로 완성한다.
- 업무 기능을 선행 구현하지 않는다. 초기 Capability·Command는 기존 Runtime 상태와 Capability 조회에 필요한 최소 Read-only 집합만 허용한다.

## 허용·제외 범위

- 허용: `services/local-service`, Tauri Rust Local Service owner/bridge, 해당 계약·Runtime·Packaging verifier, 최소 dependency/lock 변경, R1-M4-06 증거·진행·완료보고.
- 새 dependency는 표준 라이브러리와 기존 Lock으로 계약을 충족할 수 없는 경우에만 최소 범위로 추가하고, 정확 Version·보안 감사·선택 이유를 보고한다.
- 제외: Local 저장·Vector·Model·ASR·Sync 업무 구현(M5 이후), WebView에서 Loopback 직접 fetch, 공개 Cloud API 변경, UI 재설계, 고정 Port, 외부 Interface/IPv6 wildcard Listen, 기존 인증·권한 의미 변경.
- `localhost`, `0.0.0.0`, `::`, LAN 주소 Binding과 Browser에 Port·Token·Bootstrap secret 노출을 금지한다.

## Bootstrap·Process·Instance 계약

- Bootstrap은 Rust가 자식 Process에 상속한 비공개 IPC로만 전달하며 CSPRNG secret, 고유 App Instance, 부모 Process 식별·Protocol Version을 포함한다. 값은 파일·환경변수·명령행·로그·stdout ready envelope·UI State에 남기지 않는다.
- Local Service는 자신이 승인된 Rust 부모가 생성한 자식인지, 전달된 Instance와 실제 실행 Instance가 일치하는지, 부모 생존/IPC 소유가 유지되는지를 확인한다. 부모 EOF·종료·Instance 교체 시 Listener와 Token을 폐기하고 bounded 종료한다.
- Ready envelope는 Protocol·Instance·동적 Port 등 비밀이 아닌 최소 정보만 반환하고 Rust가 요청한 Instance와 정확히 일치할 때만 채택한다.
- 다른 Process가 bootstrap 구조를 복제하거나 이전 Instance 값을 재사용해도 인증되지 않아야 한다. Process/Instance 검증을 HTTP Header 문자열 하나에만 의존하지 않는다.

## 단기 Token·회전·재전송 방지

- 요청 Token은 최소 256-bit CSPRNG 또는 동등 강도의 인증된 Token으로, Service/App Instance·Capability·Command·발급/만료·고유 nonce에 결합한다.
- Bootstrap root secret은 HTTP 요청 Token으로 직접 재사용하지 않는다. Rust Process owner만 짧은 수명의 요청 Token을 만들거나 획득할 수 있고 WebView에는 전달하지 않는다.
- Token 기본 유효시간은 60초, 허용 상한은 300초로 고정한다. 만료 전후 경계, 미래 발급시각, 변조된 claim/signature, 다른 Instance·Capability·Command용 Token, 이전 Service 실행 Token을 모두 거부한다.
- 동일 nonce의 재사용은 첫 허용 요청 이후 거부한다. 검증은 상수시간 비교를 사용하고 실패 이유로 secret·claim 내부값을 반사하지 않는다.
- Token·root secret·서명·원문 Authorization은 로그·오류·증거·Crash output에 0건이어야 한다.

## Capability·Command Allowlist

- Versioned Capability Catalog와 고정 Command registry를 코드 정본으로 둔다. Route 문자열 존재만으로 명령을 실행하지 않으며 `Protocol → Instance → Token → Capability → Command → Method/Body` 순서로 모두 검증한 뒤 dispatch한다.
- 초기 허용 범위는 기존 `runtime.status.read`와 `runtime.capabilities.read`에 필요한 Read-only 명령만 둔다. Local 저장·모델·Sync 명령은 등록하지 않는다.
- 미등록 Capability/Command, 허용 Method 불일치, Capability와 Command 불일치, 중복/알 수 없는 필드, 과대 Header/Body, Transfer-Encoding, Path/Query 우회와 인코딩 변형을 작업 시작 전에 거부한다.
- 오류는 안정 code·opaque trace만 반환하고 Stack·내부 경로·Port·Process ID·secret 이름을 노출하지 않는다. 인증 실패 응답은 위조·만료·Instance 불일치를 구분해 공격자에게 알려주지 않는다.

## Loopback·Browser 차단

- 실제 Listener는 IPv4 `127.0.0.1` 하나에만 Bind한다. `0.0.0.0`, LAN Interface, IPv6 wildcard/외부 Interface Listen은 0건이어야 한다.
- Host Header를 정확히 검증하고 Proxy/Forwarded 계열, absolute-form target, 외부 Host, DNS rebinding 형태를 거부한다.
- CORS를 활성화하지 않는다. `Origin`/Browser Fetch Metadata가 있는 요청은 허용하지 않고, WebView는 Tauri invoke를 통해 Rust broker만 호출한다.
- Rust broker는 WebView가 임의 Capability·Command·URL·Header·Token을 전달하게 하지 않고 고정 Typed invoke만 제공한다. 초기 UI 공개 기능은 기존 Local Service 상태 의미를 넘지 않는다.

## TDD·필수 검증

- RED: 만료·미래·변조·다른 Instance/Command·재사용 Token, 위조 Parent/Instance, Browser Origin, 외부 Host/Interface, 미등록 Capability/Command, Method/Body/인코딩 우회가 기존 골격에서 차단되지 않거나 계약이 부재함을 먼저 증명한다.
- GREEN: 위 공격 0건 허용, 정상 Rust owner의 최소 Read-only 흐름만 성공하도록 단위·통합·실제 Process 검증을 통과한다.
- 실제 Packaged Local Service를 최소 2회 기동해 Token/Instance가 실행마다 달라지고 이전 실행 Token이 거부되며, Parent 종료 뒤 Process·Listener 0, 같은 Port 재사용 또는 새 동적 Port 재기동을 확인한다.
- `netstat` 또는 동등 OS 증거로 `127.0.0.1` Listener만 존재하고 외부 Interface Listen 0건임을 확인한다. Browser Source/Bundle에 Port·Token·내부 URL·secret 문자열 0건을 검사한다.
- Python unit/coverage/lint/type/security, Rust owner/contract, Desktop bridge/build, Local Runtime/Package, Identity·Authorization 회귀, OpenAPI 비변경, Quality/Independence를 실행한다. 정적 검사는 실제 Process/Listener 검증을 대체하지 않는다.
- 실제 GUI가 필요하면 어울1에게 넘기고 작업 중 임의 화면을 열지 않는다. GUI를 사용한 경우 종료 후 반드시 닫는다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M4-06_progress.md`에 착수, 각 단계 완료, 오류·복구, 테스트 완료, 종료 직전마다 시각·상태·변경 파일·명령/결과·원인/복구·다음 작업을 기록한다.
- Evidence는 `docs/03_evidence/release_1/R1-M4-06/`에 secret 원문 없이 저장하고, 실제/정적/Mock 증거를 구분한다.
- 완료보고는 `판정 → 판단 이유 → 조치` 순서와 표준 상태 계약으로 작성한다. 구현·검증 후 단일 Commit을 Push하고 Local/Remote SHA·Clean을 보고한다.

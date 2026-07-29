# R1-M5-03 Local 암호화 저장 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M5-03`.
- 공식 작업공간: `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`.
- Branch `codex/r1-m5-03`, 기준 HEAD `3f5f77be5b252c7d91d24ceae77c784f30b97efe`, 시작 Clean.
- 승인 정본: `AGENTS.md`, 상세 설계 0.7의 §20·§22·§23, Release 1 구현계획 0.9 §15의 R1-M5-03, 테스트계획 0.7의 M5·TP-2·TP-5 항목, 결정 `R1-D005`.
- 선행 R1-M4-06과 그 Windows Tauri·Packaged Local Service·stdin Bootstrap·Loopback 보안 계약을 재사용하고 우회하지 않는다.
- 어울2가 이 Branch와 범위의 유일한 코드 Writer다. 설계·PR·CI·Merge·완료 판정은 어울1 소유다.
- `D:\Project\Daon_User`와 `C:\tmp`의 Clone·Worktree는 읽기 전용 보존 자료이며 수정·삭제·작업 전환을 금지한다.

## 단일 목표와 사용자 완료 조건

- 목표: Windows Local-private Workspace의 Metadata·원본/산출물 File·Embedded Vector Index를 재시작 후에도 복구 가능한 암호화 저장소로 보존하고, Key가 잠김·철회·손상된 경우 평문이나 우회 경로 없이 fail-close하는 기반을 완성한다.
- 사용자는 Python·DB·File·Vector CLI를 직접 실행하지 않는다. Tauri App과 인증된 Loopback Local API를 통해 저장·조회·검색·잠금 상태를 확인한다.
- 네트워크가 차단되어도 Local-private 저장·검색은 동작하며 Cloud·External·Daon으로 자동 전송하거나 Fallback하지 않는다.
- 앱·Local Service 재시작 후 동일 Workspace 데이터와 Vector 검색 결과가 재현되고, Key 철회 후 기존 데이터는 읽을 수 없으며 새 Key를 자동 생성해 기존 암호문을 덮어쓰지 않는다.

## 의존성·실현 가능성 Gate

- 구현 전에 Windows 11, Python `3.14.3`, uv `0.11.2`, Rust `1.97.1`, Tauri `2.11.4`, PyInstaller `6.21.0` 기준으로 암호화 SQLite Driver·`sqlite-vec`·암호 Primitive·Windows Credential Manager API의 최신 공식 지원 상태, License, ARM/x64 Wheel 또는 Build, 취약점과 Packaged Sidecar 포함 가능성을 조사한다.
- `R1-D005`의 `SQLite+sqlite-vec Adapter`, OS Credential Manager, 암호화 저장 요구를 만족하는 검증된 조합만 정확 Version·Digest로 Pin한다.
- 승인 계약을 만족하지 못하면 평문 SQLite, XOR·자체 암호, 메모리 전용 Vector, 일반 JSON/File, 환경변수·명령행 Key, 사용자 Profile의 평문 Key 같은 쉬운 대체로 진행하지 않는다. 증거와 영향·대안을 `BLOCKED`로 어울1에게 반환한다.
- 새 의존성은 Python 3.14.3·Windows Packaging·License·취약점 검증과 Lockfile 반영을 완료한다. 기능·보안 경계를 바꾸는 대안은 신산님 승인 전 구현하지 않는다.

## Key·잠금 계약

- Root/Master Key는 Windows Credential Manager에 현재 Windows 사용자·Daon App 범위로 저장한다. Log·Error·Audit·명령행·환경변수·Source·설정파일·Evidence에 Key 원문을 남기지 않는다.
- Workspace·영역별 Data Encryption Key를 분리하고 Master Key로 Versioned Wrap한다. Key ID·Version·Algorithm·생성/회전 시각·상태만 Metadata로 보존한다.
- Tauri가 OS Secure Store 접근을 소유하고 Local Service에는 기존 stdin Bootstrap 또는 동등한 상속 Pipe로 실행에 필요한 최소 Key Material만 전달한다. Key를 Process Argument, 공개 IPC, Loopback 응답, Browser Bundle과 Child Environment에 넣지 않는다.
- Local Service는 Key를 필요한 수명 동안만 보유하고 잠금·종료·재시작에서 참조를 제거한다. Debug·Exception·Crash Dump 친화 출력에 Key가 나타나지 않아야 한다.
- 새 저장소 최초 생성, 기존 저장소 Unlock, 잠금, 자동 잠금, 재시작 Unlock, Key 철회 상태를 분리한다. 기존 암호문이 있는데 Credential이 없거나 틀리면 `LOCAL_KEY_UNAVAILABLE` 또는 승인된 안전 코드로 잠그고 새 Key를 자동 생성하지 않는다.
- Key 철회는 Credential 제거와 현재 Session 무효화를 포함한다. 정식 Key 회전·복구 UI와 Backup은 R1-M5-07이 승계하므로 이번 작업에서 복구 불가능한 임의 회전·삭제 API를 공개하지 않는다.

## 암호화 SQLite·File Store 계약

- Local Metadata SQLite는 검증된 저장 암호화를 적용한다. DB 본문, WAL, SHM, Journal, Temporary File과 Error Dump에 사용자 평문 Canary가 없어야 한다.
- Schema는 최소 Workspace·Area·Object/File Reference·Digest·Byte Size·검증 MIME·Version·Created/Updated·Key Version·Vector Reference·상태를 가진다. R1-M5-04의 전체 데이터 정본 Entity를 선점하지 않는다.
- 모든 DB 연결은 Key 설정과 Cipher/Integrity 확인 전 Query를 허용하지 않는다. 잘못된 Key·손상 Header·Downgrade·암호화되지 않은 기존 DB는 fail-close한다.
- File Store는 Server가 생성한 불투명 ID와 Workspace·Area 경계를 사용하고 파일명·상대경로·`..`·절대경로·Reparse Point·Symlink·Hardlink·Unicode 혼동·TOCTOU 탈출을 차단한다.
- File 암호문은 Versioned Header, Algorithm·Key Version, 무작위 Nonce, 인증 Tag, 원문 SHA-256·Byte Size·Content Type Reference를 가진다. AEAD와 CSPRNG만 사용하고 Nonce 재사용을 금지한다.
- 저장은 같은 Volume의 임시 암호문 작성→Flush/Sync→검증→Atomic Replace 순서로 수행한다. 중단·Disk Full·Checksum/Tag 불일치·부분 파일을 성공으로 표시하지 않는다.
- Get은 현재 Local Workspace Scope와 Key 상태를 다시 검증한 뒤 제한된 Stream으로 복호화한다. 무제한 임시 평문 파일, Public URL, Browser 직접 File 경로를 만들지 않는다.

## Embedded Vector Adapter 계약

- Local Search는 Port와 `sqlite-vec` Adapter로 분리한다. Vector Dimension·Metric·Model/Artifact Digest·Embedding Version·Source/Object Version·Workspace·Area·Key Version을 기록한다.
- Vector Row와 Index가 다른 Workspace·Area에서 검색되지 않아야 하며 Top-K·Filter·삭제/갱신 대상 Version이 명시적이어야 한다.
- Vector 원문과 연결 Metadata도 저장 시 암호화 경계를 벗어나지 않는다. Extension이 암호화 DB와 같은 Connection/Key 경계를 사용하지 못하면 구현을 우회하지 말고 `BLOCKED`로 반환한다.
- 이번 작업은 승인된 고정 Test Vector를 사용한 Adapter 저장·검색·재시작·격리만 구현한다. 실제 Embedding Model 호출과 Retrieval 정책은 M6가 소유한다.

## Local API·운영 상태 계약

- 기존 Authenticated Loopback 계약, Host Header·Token TTL·Nonce Replay·Capability/Command 검사와 Parent Process 수명주기를 유지한다.
- 저장 기능은 최소 내부 Capability/Command 계약으로만 노출하고 공개 Web/Cloud API를 추가하지 않는다. 요청마다 Workspace·Area·Lock 상태를 검증한다.
- 상태는 `locked | ready | degraded | corrupted | key_unavailable`처럼 사용자가 조치 가능한 안전 상태와 코드로 제공하되 Path·Key ID 전체·Stack·암호 Provider 원문을 노출하지 않는다.
- 암호화 저장 장애가 Local Service Process를 불필요하게 종료시키지 않되, 잠김·손상 상태에서 Read/Write/Search를 성공시키지 않는다.

## 허용·제외 범위

- 허용: Local Storage/Key/File/Vector Port·Adapter, 최소 Metadata Migration, Windows Credential Manager Adapter, stdin Bootstrap 확장, 인증된 Local API 내부 계약, Tauri/Sidecar Packaging, Unit·Integration·Failure Injection·실제 Windows 설치 검증, Architecture·Evidence·Progress·완료보고.
- 제외: Cloud Sync·Copy/Publish(R1-M5-05), 전체 Source/Run/RuleSet/Model/Studio 정본(R1-M5-04), 삭제·Retention·Legal Hold(R1-M5-06), Backup·Key 복구/회전 정식화(R1-M5-07), 실제 Embedding/LLM/ASR, 공개 Web API·UI 확장, 운영 Oracle 배포.
- 기존 M4 Auth·Authorization·Audit·Notification, Browser same-origin, Tauri Process/Job Object, 설치·서명 계약을 암묵적으로 변경하지 않는다.

## TDD·필수 검증

- RED: 암호화 DB/File/Vector Adapter와 OS Secure Store 부재, 잘못된 Key, Key 철회, DB/WAL·File 암호문 손상, 경로 탈출, 부분 Write, 재시작, Workspace 교차 검색의 기존 실패를 먼저 증명한다.
- Storage: Create→Write→Restart→Unlock→Read, Digest/Size/Tag 일치, 동일 요청 멱등, Corruption/Partial Write fail-close, 다른 Workspace·Area 조회 0건을 검증한다.
- Plaintext Scan: 고유 Canary를 DB/WAL/SHM/Journal/Temp/File/Vector Index/Log/Crash/Error/Evidence 전체에서 Byte Scan해 평문 0건을 증명한다.
- Key: Credential 생성·재사용·잠금·자동 잠금·재시작·철회, 잘못된 Windows 사용자/Key, 기존 데이터+Credential 부재 시 자동 재생성 0건, Argument/Environment/Log 노출 0건을 실제 Windows Process에서 검증한다.
- Vector: 고정 Test Vector의 Insert·Top-K·Filter·Restart, Workspace·Area 교차 결과 0건, Dimension/Metric/Model Version 불일치 차단을 검증한다.
- Network: Windows Firewall 또는 검증 가능한 차단 환경에서 Local-private 저장·검색을 수행하고 외부 Connection·DNS·Cloud/Daon 호출 0건을 Process/Network Evidence로 남긴다.
- Packaging: 실제 Tauri Installed/Packaged App에서 Windows Credential Manager·Sidecar·암호화 Extension/DLL 검색 경로를 검증한다. 화면을 사용하면 검증 종료 즉시 앱·Simulator·Browser를 모두 닫아 신산님의 화면을 점유하지 않는다.
- 회귀: Local Service Python, Rust/Tauri Lifecycle, Windows Desktop Build, API/BFF, Web Build, Quality Gate, Independence를 실행한다.
- ysna-server는 Portable Contract·Package 재현 검증에만 사용하며 Windows Credential Manager·Installed App 증거를 대체하지 않는다. 서버를 사용하면 `/home/ubuntu/deploy/daon-user` 아래 격리 자원만 사용하고 종료 시 잔여 0·공용 자원 불변을 확인한다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M5-03_progress.md`에 착수, 영향·의존성 조사, 실현 가능성 Gate, RED, Key/SQLite/File/Vector/API 구현, 로컬 검증, Commit·Push, Windows 설치·암호화·재시작·철회·Network 검증, 서버 Portable 검증, 오류·복구와 종료 직전을 즉시 기록한다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-03/`, 완료보고는 `docs/04_test_reports/release_1/R1-M5-03_completion_report.md`에 작성한다.
- 결과는 `판정 → 판단 이유 → 조치`와 `COMPLETED | FAILURE_REPORT | INCOMPLETE | BLOCKED` 계약으로 반환한다. 기본 테스트와 실제 Windows Secure Store·Installed App 증거가 없으면 `COMPLETED`로 보고하지 않는다.
- 단일 구현 Commit과 Evidence-only Commit을 구분하고, 종료 전 Local HEAD·Origin Branch·검증 exact SHA, Working Tree Clean, 잔여 Process·Listener·App 0, 정식 실패보고 횟수를 보고한다.

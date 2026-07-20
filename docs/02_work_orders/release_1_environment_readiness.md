# Release 1 환경·자격·증거 준비표

## 상태 정의

- `READY`: 식별 정보와 검증 방법이 확인됨
- `PARTIAL`: 일부는 확인됐으나 Release 증거를 만들 조건이 부족함
- `EXTERNAL_BLOCKED`: 계정·장치·계약 등 외부 입력이 필요함
- `NOT_STARTED`: 선행 Gate 이후 준비할 항목

| 영역 | 필요한 환경·자격 | 현재 상태 | 필수 증거 | 관련 결정/WO |
| --- | --- | --- | --- | --- |
| Git 기준선 | 독립 Repo, 원격, 문서 기준 Commit, 보호 Branch | PARTIAL | `git status`, remote, 기준 Commit·Manifest | D002, M0-A01, M1-01 |
| Web | Windows 11, Chrome·Edge, 실제 Network/Console | PARTIAL | 실제 클릭, same-origin URL, Process 재기동 | D001, M3-01 |
| Windows App | Windows 11 PC, Tauri/Rust, 설치·서명 환경 | PARTIAL | EXE 설치·클릭·Process·외부 Listen 0 | D001·D011·D012, M3-02~03 |
| WSL 통합 | WSL 서버, PostgreSQL/Object/Worker/BFF, `WSL-server` Domain | PARTIAL | Git 배포 Commit, Service Health, Browser Network | D003·D005, M5~M7 |
| Oracle Cloud | OCI Seoul, Network, DB/Object/Vault/Compute, Domain/TLS | EXTERNAL_BLOCKED | 사전 승인 ID, 배포 Commit, Rollback·Health | D003·D005·D009, M9 |
| Android | Android 12+ 실기기, Keystore, Push/Test 계정 | EXTERNAL_BLOCKED | APK 설치, 권한·Background·Offline 실제 클릭 | D001·D011·D012, M3-05 |
| iOS | macOS Build Host/CI, Xcode 26.6, CocoaPods 1.16.2, Team/Signing/Profile, Device/Simulator | EXTERNAL_BLOCKED | Archive/설치 Build, 권한·Background 실제 클릭 | D001·D002·D011·D012, M3-06 |
| Identity | OIDC Test Tenant, Client, Redirect URI, 조직 사용자 | EXTERNAL_BLOCKED | 실제 로그인·갱신·철회·Step-up | D004, M4-03 |
| Local LLM | 대상 Windows 장비 CPU/GPU/RAM/VRAM/Disk, 허용 Model Artifact | EXTERNAL_BLOCKED | Hardware 진단, Digest, 설치·시험·Rollback | D006, M6-03 |
| External/Internal LLM | Provider 계정, 모델 Allowlist, 비용·지역·데이터 정책 | EXTERNAL_BLOCKED | 실제 Route=Model=Network=Audit, 장애/Fallback | D006, M6-11~14 |
| ASR/Embedding/Reranker | Local/Cloud 후보와 License, Artifact/Deployment | EXTERNAL_BLOCKED | 실제 오디오·Vector 품질·Version 계보 | D006, M6-08·M6-14 |
| Daon | Sandbox URL, 표준 API 계약, Credential, RuleSet/지식 Fixture | EXTERNAL_BLOCKED | 연결/미연결/장애, Version·Timeout·Audit | D007, M6-15 |
| 인터넷 검색 | Provider/License/Credential, Domain Policy, Safe Fetch Fixture | EXTERNAL_BLOCKED | SSRF 차단, Citation 원문, 비용·Audit | D008, M6-09 |
| Backup/Restore | 전용 Tenant/Workspace Fixture, 격리 Restore Target | NOT_STARTED | RPO/RTO 측정, 현재 권한·계보 재검증 | D009, M5-07·M9 |

## Toolchain Pin 기준

R1-D002의 버전은 M1에서 버전 파일과 Lockfile에 기록하고 CI가 다르면 실패하게 한다. React Native는 `0.86.x` 계열 중 최초 재현 Build에서 정확한 Patch를 고정한다. Xcode·CocoaPods는 승인된 macOS Host에서만 검증하며 Windows 대체 증거를 허용하지 않는다.

## G0 준비 판정

G0-BASELINE은 `APR-G0-BASELINE-20260720-01`로 승인되었다. 문서·Git·Web/Windows 로컬 기준선으로 M1을 시작할 수 있다. 외부 Provider·모바일·서명·macOS·OCI 자격은 준비되지 않았으므로 각 관련 Work Order는 실제 증거 환경이 확보될 때까지 `BLOCKED`로 유지한다.

# Daon 사용자 프로그램 Release 1 결정 기록

## 문서 정보

| 항목 | 값 |
| --- | --- |
| 기준일 | 2026-07-20 |
| 소유자 | 어울1 · 설계·기술 책임자 |
| 승인자 | 신산님 |
| 기준 설계 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` |
| 기준 계획 | `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` |
| 상태 | G0-BASELINE 승인 · `APR-G0-BASELINE-20260720-01` |

상태는 `확정`, `승인 요청`, `외부 차단`, `범위 제외 승인` 중 하나만 사용한다. `외부 차단`은 요구사항 삭제가 아니며 필요한 계정·장치·계약이 확보될 때까지 관련 Work Order가 시작 불가함을 뜻한다.

## M0 결정표

| ID | 상태 | 결정 또는 권고 기준선 | 후속 조치·승인 |
| --- | --- | --- | --- |
| R1-D001 | 확정 | Windows 11 23H2 이상, Chrome·Edge 최신 2개 주 버전, Android 12 이상, iOS 17 이상을 R1 최소 지원선으로 한다. | `APR-G0-BASELINE-20260720-01` |
| R1-D002 | 확정 | Node.js `24.18.0` LTS, npm `11.12.1`, Corepack `0.35.0`, Python `3.14.3`, uv `0.11.2`, Rust `1.97.1`, Tauri CLI `2.11.4`, React Native `0.86.0`, PostgreSQL `18.4`, Xcode `26.6`, CocoaPods `1.16.2`. Web 공통 기준은 Next.js `16.2.10`, React `19.2.7`, TypeScript `7.0.2`로 하며 Lockfile·CI·버전 파일로 정확히 Pin한다. | `CHG-R1-M1-03-001` · 레지스트리·배포 채널 사전검증에 따른 C1 기술 정정 |
| R1-D003 | 확정 | R1 Pilot은 Hybrid로 한다. 로컬 개발과 Windows Local-private, WSL 통합 환경, OCI Seoul Managed Cloud 운영 경로를 포함한다. On-prem 정식 배포는 R1 Pilot 범위에서 제외한다. | `APR-G0-BASELINE-20260720-01` |
| R1-D004 | 외부 차단 | OIDC Authorization Code+PKCE와 조직 Provisioning Adapter를 계약으로 고정한다. 실제 Identity Provider·Tenant·Client 등록값은 아직 미제공이다. | IdP 종류와 테스트 Tenant/Client 제공 필요 |
| R1-D005 | 확정 | Cloud DB·Vector는 PostgreSQL+pgvector, Object는 S3 호환 Adapter(MinIO 개발/WSL, OCI Object Storage 운영), Durable Job은 PostgreSQL Outbox+Worker, 일시 Lease/Cache는 Valkey, Cloud Secret은 OCI Vault, Windows Local Secret은 OS Credential Manager, Local Vector는 SQLite+sqlite-vec Adapter를 사용한다. | 어울1 기술 결정. M1/M5에서 라이선스·복구·Windows 패키징 검증 |
| R1-D006 | 외부 차단 | Local/Internal/External LLM·ASR·Embedding·Reranker는 Allowlist와 Deployment Health 계약을 사용한다. 실제 모델명·라이선스·GPU/CPU 기준은 하드웨어와 Provider 계정 미확보로 미고정이다. | 장비 사양·허용 Provider·예산 제공 후 신산님 중요 위험 승인 |
| R1-D007 | 외부 차단 | Daon Connector는 표준 API 선택 연동, Read-only 승인 지식과 Versioned RuleSet 실행 계약을 유지한다. Sandbox URL·자격·호환 버전은 미제공이다. | Daon Sandbox 계약·Credential 제공 필요 |
| R1-D008 | 외부 차단 | 검색 Provider Adapter, Domain/URL Allowlist, DNS/IP 재검증, Redirect 제한, 다운로드 크기·형식 제한, Safe Fetch를 의무화한다. 실제 Provider·License·Credential은 미선정이다. | Provider·비용·허용 도메인 신산님 승인 필요 |
| R1-D009 | 확정 | OCI Seoul을 기본 Region으로 한다. 원본·산출물 기본 보존은 Workspace 정책, 삭제 유예 30일, Audit 1년, RPO 15분, RTO 4시간을 Pilot 기본값으로 한다. Legal Hold가 있으면 삭제보다 우선한다. | `APR-G0-BASELINE-20260720-01` |
| R1-D010 | 확정 | Pilot 기본 한도는 파일당 100MB, Workspace당 20GB, 사용자 동시 Run 2개, 조직 동시 Run 20개로 한다. 상호작용 API p95 3초 이내(비동기 작업 제외), 비용 한도는 조직 관리자가 설정하고 초과 시 설계의 `COST_LIMIT_EXCEEDED`를 적용한다. | `APR-G0-BASELINE-20260720-01`; 실제 Pilot 규모 변경은 변경 통제 적용 |
| R1-D011 | 외부 차단 | Windows Code Signing, Android Keystore, Apple Developer Team·Signing·Provisioning, Push 계정이 아직 확인되지 않았다. | 계정·인증서·보관 책임자 제공 필요 |
| R1-D012 | 외부 차단 | Web·Windows 로컬 검증은 현재 장비에서 준비 가능하다. Android 실기기, iOS 실기기/Simulator, macOS Build Host 또는 macOS CI Runner의 식별·접근 증거는 미확인이다. | 장치·Build Host 접근 제공 필요 |
| R1-D013 | 확정 | ConflictPolicyVersion 자동 판정, 검토자 상향, 중요 충돌 미해결 시 최종화 차단 | `APR-G0-DESIGN-20260720-01` |
| R1-D014 | 확정 | 가중치 기본 1.0, 범위 0.5~2.0, 0.1 단위, 개별 Source→그룹→유형→기본값 중 최근접 하나 적용 | `APR-G0-DESIGN-20260720-01` |
| R1-D015 | 확정 | 비용 초과는 `policy_blocked/COST_LIMIT_EXCEEDED`, 동일 Frozen Context 자동 재시도 금지 | `APR-G0-DESIGN-20260720-01` |
| R1-D016 | 확정 | 모바일 편집은 제목·기존 텍스트·단순 표 Cell·검토·승인 화이트리스트 | `APR-G0-DESIGN-20260720-01` |
| R1-D017 | 확정 | 외부 전송·영역 이동·생산 지식 등록·정책 변경·장치 철회·영구 삭제·Restore에 Step-up | `APR-G0-DESIGN-20260720-01` |
| R1-D018 | 확정 | Audio-capable LLM 또는 ASR+LLM 의미 이해와 시간 근거 검증, ASR-only ready 금지 | `APR-G0-DESIGN-20260720-01` |
| R1-D019 | 확정 | `waiting_model`은 제한 자동 재큐와 권한 사용자 수동 재처리, 새 ProcessingRun과 중복 억제 | `APR-G0-DESIGN-20260720-01` |
| R1-D020 | 확정 | 과거 OutputVersion 불변 보존, 모든 접근·전달·등록·재실행은 현재 권한으로 재검증 | `APR-G0-DESIGN-20260720-01` |

## G0 판정

- 설계 미정의 항목 R1-D013~020은 모두 확정되었다.
- R1-D001·D003·D009·D010은 신산님의 `APR-G0-BASELINE-20260720-01`로 확정되었다.
- R1-D004·D006~D008·D011·D012는 외부 차단으로 분류되어 미정 상태를 숨기지 않는다. 관련 Work Order는 자격·장치·계약 확보 전 `BLOCKED`다.
- G0-BASELINE은 2026-07-20 승인되었다. 외부 차단 항목은 완료로 간주하지 않으며 관련 Work Order만 조건부 `BLOCKED`로 유지한다.

## M1 기술 정정 기록

| 변경 ID | 일자 | 등급 | 변경 | 근거와 영향 |
| --- | --- | --- | --- | --- |
| `CHG-R1-M1-03-001` | 2026-07-20 | C1 | Python `3.14.6→3.14.3`, Tauri CLI `2.11.5→2.11.4`, React Native `0.86.x→0.86.0`; npm·Corepack·uv·Next.js·React·TypeScript 정확 버전 추가 | Python 배포 목록과 npm Registry에서 승인안 일부가 존재하지 않음을 R1-M1-03 사전검증으로 확인했다. 제품 범위·요구사항·공개 API·데이터·보안 경계는 바뀌지 않으며 재현 가능한 Toolchain 계약만 정정한다. |

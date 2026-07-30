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
| R1-D003 | 확정 | R1 Pilot은 Hybrid로 한다. 로컬 개발과 Windows Local-private, ysna-server 격리 개발·통합 환경, OCI Seoul Managed Cloud 운영 경로를 포함한다. WSL은 선택적 대체 환경이고 On-prem 정식 배포는 R1 Pilot 범위에서 제외한다. | `APR-G0-BASELINE-20260720-01` + `APR-DEVENV-YSNA-20260720-01` |
| R1-D004 | 외부 차단 | OIDC Authorization Code+PKCE와 조직 Provisioning Adapter를 계약으로 고정한다. 실제 Identity Provider·Tenant·Client 등록값은 아직 미제공이다. | IdP 종류와 테스트 Tenant/Client 제공 필요 |
| R1-D005 | 확정 | Cloud DB·Vector는 PostgreSQL+pgvector, Object는 S3 호환 Adapter(MinIO 개발/ysna-server 통합, OCI Object Storage 운영), Durable Job은 PostgreSQL Outbox+Worker, 일시 Lease/Cache는 Valkey, Cloud Secret은 OCI Vault, Windows Local Secret은 OS Credential Manager, Local Vector는 SQLite+sqlite-vec Adapter를 사용한다. | 어울1 기술 결정. M1/M5에서 라이선스·복구·Windows 패키징 검증 |
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
| R1-D021 | 확정 | 개발·통합은 로컬 수정·검증→Git Push→`/home/ubuntu/deploy/daon-user` 격리 배포→전용 PostgreSQL 18.4 Migration→서버 Test→PR Merge로 한다. 기존 `shared-db`와 `common/netdata/proxy` 사용·변경을 금지하고 ARM64/Multi-arch 호환성을 검증한다. WSL은 선택적 대체 환경이며 OCI 운영 G9 승인은 유지한다. | `APR-DEVENV-YSNA-20260720-01` |
| R1-D022 | 확정 | Next `16.3.0-canary.93`을 Sharp·PostCSS 취약점 제거를 위한 Release 1 개발·검증 전용 임시 보안 브리지로 사용한다. 안정판 전환 전 운영 Release를 금지하며 안전 범위를 포함한 안정판 출시 시 동일 회귀·Quality Gate 후 즉시 교체한다. | `APR-R1-M2-06-SEC02-20260722-01` · 신산님 승인 |
| R1-D023 | 확정 | 기존 범용 앱 설정 진입을 보존하고 알림 전용 공개 API 진입을 별도로 제공한다. iOS 16 이상은 `UIApplication.openNotificationSettingsURLString`, iOS 15.1은 기존 `openSettingsURLString` Fallback을 사용하며 비공개 URL·Settings/TCC 직접 조작은 금지한다. | 신산님 승인 · 2026-07-28 |

| R1-D024 | 확정 | R1-M4-07은 공개 `GET /api/v1/notifications`, `GET·PATCH /api/v1/notifications/{id}`, `GET /api/v1/inbox`를 추가한다. Notification 읽음 Write는 ETag·멱등성을 적용하고, 대상·Deep Link는 현재 권한으로 재검증한다. Inbox는 소유 Domain 요청의 읽기 Projection이며 실제 전송은 In-app부터 구현하고 Push·Email과 영속 DB Adapter는 후속 경계로 유지한다. | 신산님 승인 · `APR-R1-M4-07-NOTIFICATION-API-20260729-01` |
| R1-D025 | 확정 | R1-M5-05는 SyncOperation 생성·조회, Step-up 승인, 승인 항목의 재개 가능한 Transfer Batch, 사용자 충돌 해결 공개 API를 추가한다. 원본 Local-private 영역·Version은 불변으로 보존하고 무승인 전송·자동 병합·자동 덮어쓰기를 금지한다. | 신산님 승인 · `APR-R1-M5-05-SYNC-API-20260730-01` |

## G0 판정

- 설계 미정의 항목 R1-D013~020은 모두 확정되었다.
- R1-D001·D003·D009·D010은 신산님의 `APR-G0-BASELINE-20260720-01`로 확정되었다.
- R1-D004·D006~D008·D011·D012는 외부 차단으로 분류되어 미정 상태를 숨기지 않는다. 관련 Work Order는 자격·장치·계약 확보 전 `BLOCKED`다.
- G0-BASELINE은 2026-07-20 승인되었다. 외부 차단 항목은 완료로 간주하지 않으며 관련 Work Order만 조건부 `BLOCKED`로 유지한다.

## M1 기술 정정 기록

| 변경 ID | 일자 | 등급 | 변경 | 근거와 영향 |
| --- | --- | --- | --- | --- |
| `CHG-R1-M1-03-001` | 2026-07-20 | C1 | Python `3.14.6→3.14.3`, Tauri CLI `2.11.5→2.11.4`, React Native `0.86.x→0.86.0`; npm·Corepack·uv·Next.js·React·TypeScript 정확 버전 추가 | Python 배포 목록과 npm Registry에서 승인안 일부가 존재하지 않음을 R1-M1-03 사전검증으로 확인했다. 제품 범위·요구사항·공개 API·데이터·보안 경계는 바뀌지 않으며 재현 가능한 Toolchain 계약만 정정한다. |
| `CHG-R1-DEVENV-001` | 2026-07-20 | C2 | WSL 필수 통합을 ysna-server 격리 개발·통합 흐름으로 대체 | 신산님이 서버 접근·배포 루트와 새 실행 순서를 승인했다. 제품 기능·공개 API·데이터 계약·보안 경계는 유지하며 개발 배포 대상·격리·Migration·Merge Gate와 ARM64 위험을 명시한다. |

## M2 임시 보안 브리지 기록

| 변경 ID | 일자 | 등급 | 변경 | 근거와 종료 조건 |
| --- | --- | --- | --- | --- |
| `CHG-R1-M2-06-SEC02-001` | 2026-07-22 | C2 | R1-D002는 삭제·수정하지 않고 Next만 `16.3.0-canary.93` exact로 개발·검증 기준선에 임시 동기화 | 안정판 Next 16.2.11까지 취약 PostCSS·Sharp 범위를 유지해 정상 Tree와 Audit 0을 함께 만족하지 못했다. Canary는 개발·검증 및 ysna-server 격리 테스트에만 사용하고 운영 Release를 금지한다. 안전 범위를 선언한 안정판 출시 즉시 동일 21/98·Lint·Build·Runtime Smoke·공통 Gate를 통과한 뒤 교체한다. |

## M3 승인 결정 기록

| 변경 ID | 일자 | 등급 | 변경 | 근거와 영향 |
| --- | --- | --- | --- | --- |
| `CHG-R1-M3-06-IOS-SETTINGS-001` | 2026-07-28 | C2 | iOS 알림 설정 전용 공개 API와 iOS 15.1 범용 설정 Fallback을 R1-D023으로 고정 | 신산님 명시 승인에 따라 Phase A Simulator 접근성을 보강했다. 기존 범용 설정 기능은 보존하고 비공개 URL·TCC 조작은 허용하지 않는다. |

## M4 승인 결정 기록

| 변경 ID | 일자 | 등급 | 변경 | 근거와 영향 |
| --- | --- | --- | --- | --- |
| `CHG-R1-M4-07-NOTIFICATION-API-001` | 2026-07-29 | C2 | Notification 목록·단건·읽음 전이와 Inbox Projection 공개 API를 R1-D024로 고정 | 신산님 명시 승인. M4 Notification 기반을 Web BFF·Native Gateway가 공유할 공개 계약으로 만들고, 권한·ETag·멱등성·Audit·Trace를 적용한다. Push·Email과 DB 영속화는 승인되지 않은 성공으로 가장하지 않고 후속 Adapter 경계를 유지한다. |

## M5 승인 결정 기록

| 변경 ID | 일자 | 등급 | 변경 | 근거와 영향 |
| --- | --- | --- | --- | --- |
| `CHG-R1-M5-05-SYNC-API-001` | 2026-07-30 | C2 | Sync·Copy/Publish의 공개 API 5종과 승인·재개 전송·충돌 선택 계약을 R1-D025로 고정 | 신산님 명시 승인. §6.3의 5단계와 §21.3의 승인 항목만 Sync·자동 덮어쓰기 금지를 API·저장 계약으로 구현한다. 실제 M6 재색인 완료는 범위에서 제외한다. |

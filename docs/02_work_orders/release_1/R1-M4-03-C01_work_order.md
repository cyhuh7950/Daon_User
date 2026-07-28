# R1-M4-03-C01 Identity 보안 계약 중대 보완 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-03-C01`.
- Branch `codex/r1-m4-03`, 기준 HEAD `a64ae006d10c342161f485d44ac0e10190c86da6`, 시작 Clean.
- 상세 설계 v0.7, 구현계획 v0.9 M4-03, 테스트계획 v0.7, R1-M4-03 정본과 어울1 독립 보안검토 지시를 적용한다.
- 어울2가 이 Worktree와 작업 범위의 유일한 Writer다. PR·CI·Merge는 어울1 소유다.

## 단일 목표

명시적 Session 철회, Access/Refresh 거부 Audit, Device trust 성공·binding 거부 Audit을 기존 OIDC·Session·Device·Step-up 계약에 추가하고 정보 비노출·원자성·race fail-close를 증명한다.

## 허용·제외 범위

- 허용: `identity.py`, Identity tests/export, Identity OpenAPI/verifier/evidence, Identity Architecture·API README, M4-03/C01 작업·진행·완료보고.
- 제외: Audit Core 변경, UI, 실제 HTTP Runtime, DB Migration/PostgreSQL, Lockfile, 외부 의존성, trust 승격의 신규 Step-up 요구.
- 기존 OIDC PKCE·digest-only 저장, Refresh rotation/replay, Device revoke, 최소 7종 Step-up, M4-02 Audit 계약을 보존한다.

## 명시적 Session 철회 계약

- `device_session_or_sync_key_revoke` Step-up을 actor·현재 session/device·tenant·action·target session·policy에 결합한다.
- 대상 Session과 연관 Refresh family를 단일 transaction에서 철회한다.
- 다른 tenant 또는 존재하지 않는 Session은 열거 불가능한 동일 안전 오류로 거부한다.
- 성공 뒤 대상 access와 refresh는 모두 안정적인 401을 반환한다.
- 철회와 Refresh rotation race는 어느 순서에서도 철회 상태로 수렴하고 credential을 살리지 않는다.

## 거부·만료 Audit 계약

- Access: `ACCESS_INVALID`, `ACCESS_EXPIRED`, `SESSION_REVOKED`를 denied/expired Audit으로 남긴다.
- Refresh: `REFRESH_INVALID`, `REFRESH_EXPIRED`, `SESSION_REVOKED`를 denied/expired Audit으로 남긴다.
- 알려진 credential은 tenant·actor·session/family 계보, 미지 credential은 `identity-public`·anonymous 계보를 쓴다.
- 오류·Audit·SQLite에 raw credential·digest·Provider/DB 내부값을 포함하지 않는다.
- Audit append 실패는 성공이나 상태 완화로 바뀌지 않고 fail-close한다. 외부 오류는 원래 credential 정보 비노출 의미를 유지한다.

## Device trust Audit 계약

- 신뢰 성공과 현재 Session에 결합되지 않은 Device 거부를 안전 Audit으로 남긴다.
- 신뢰 성공의 DB 변경은 Audit append 실패 시 rollback한다.
- 기존 설계대로 trust 승격 자체에 새 Step-up을 요구하지 않는다.

## TDD·검증

- RED 신규 테스트: Session revoke step-up 누락·binding·tenant isolation·성공, access/refresh invalid·expired·revoked Audit, trust 성공·binding denied Audit, raw/digest 비노출, revoke/refresh race, Audit 실패 rollback·안전 유지.
- 기존 테스트 기대를 약화하거나 삭제하지 않는다.
- Identity write/no-write, Audit/OpenAPI no-write, Python compile/export, Workspace, Independence, Toolchain, relevant Quality capability를 실행한다.
- 장시간 전체 Gate는 R1-M4-03 기준선 제한을 재사용하며 강제 반복하지 않는다.

## 진행·보고

`docs/04_test_reports/release_1/R1-M4-03-C01_progress.md`에 착수, RED, GREEN, 오류·복구, 검증, 종료 직전 상태를 기록한다. 완료보고 후 단일 보완 Commit을 같은 Branch에 Push하고 Local/Remote SHA·Clean을 보고한다.

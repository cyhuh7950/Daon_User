# R1-M2-07-C03 어울1 직접 구현 기록

## 판정

동일 `issue_id=R1-M2-07-I001`에서 `INCOMPLETE` 합계 3회에 도달해 어울2의 쓰기를 중지했다. 신산님이 2026-07-22 어울1 직접 구현을 승인했으므로 `DIRECT_IMPLEMENTATION`으로 C03을 인수한다.

## 승인된 최소 범위

- `operator` NavigationPersona를 MembershipRole 또는 Recovery 쓰기 권한으로 해석하지 않는다.
- Recovery Preview는 정본 MembershipRole을 먼저 검증하고 `organization_admin`만 허용한다.
- `operator` + Capability + 유효 Step-up/G9 공격을 회귀 테스트로 고정한다.
- Operations/Recovery Model·전용 Test·Adapter 계약·R1-M2-07 증거와 진행 기록만 갱신한다.

## 금지 범위

실제 API·Queue·DB·Backup·Restore·Update·배포, Dependency·Lockfile·Toolchain·CI, 기존 화면·시각 계약은 변경하지 않는다. 기존 C01·C02 Green을 완화하거나 삭제하지 않는다.

## 완료 조건

신규 공격 RED→GREEN, 전용 Test, 전체 순차 회귀, Workspace Lint, Production Build, 공통 Quality Gate, Manifest Hash·Byte와 금지 범위 검사가 모두 통과해야 한다.

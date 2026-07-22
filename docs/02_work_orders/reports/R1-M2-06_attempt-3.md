BLOCKED | R1-M2-06-I001 | C02 호출자 역할 주입 권한 우회 보정 | 현재 활성 Membership의 Role·Grant만 권한 정본으로 사용하고 호출자 Role 불일치를 거부했다 | 전용 21/21·전체 98/98·Lint·Build PASS, 공통 Gate는 dependency audit 1건만 FAIL | 기존 Next/Sharp/PostCSS 기준선 취약점 | 어울1 별도 dependency remediation 판단 및 Gate 재실행

# R1-M2-06 Attempt 3 결과보고

## 판정

`BLOCKED`

C02 기능 결함은 수정·회귀 검증을 완료했다. 그러나 원 작업지시서의 완료 조건인 공통 Quality Gate가 기존 Production Dependency Audit 한 건으로 실패했고, C02 작업지시서가 의존성·Lockfile·Toolchain·CI 변경을 금지하므로 완료로 선언하지 않는다.

## 판단 이유

### C02 결함 재현과 최소 보정

- 수정 전 기존 전용 테스트는 20/20 PASS였다.
- 활성 `viewer` Membership에 Caller 입력 `role/persona/grants=organization_admin`을 주입하는 회귀 테스트를 먼저 추가했다.
- RED는 20 PASS / 1 FAIL이었다. 안전 거부 Code가 없고 정책 Preview가 생성되어 독립 검토의 원인을 그대로 재현했다.
- `authorizeAccountAction`은 현재 활성 Membership·Tenant·Workspace와 Membership Grant만 권한 정본으로 사용한다.
- Caller가 전달한 Role이 현재 Membership Role과 다르면 `AUTHORIZATION_DENIED`로 거부한다.
- `preview-policy-change`는 현재 Membership이 `organization_admin`일 때만 Preview를 만들며 거부 시 기존 Preview도 제거한다.

### 부정·정상 경로 결과

- Viewer의 Role·Persona·Grant 주입: 거부, `policyPreview=null`, Domain 상태·외부 호출·Audit 불변.
- 정상 조직 관리자: 정책 Preview 성공, 실제 API Write `0건` 유지.
- C01의 Step-up 권한 재검사, Route Projection, Browser 증거 계약은 재작성하지 않았고 전체 회귀로 보존을 확인했다.
- C02는 순수 Domain 권한 경계 보정이므로 기존 수락된 Production Browser PNG/JSON을 새 주장으로 다시 쓰지 않았다.

### 테스트 결과

| 검증 | 결과 |
| --- | --- |
| 전용 Account/Security | 21/21 PASS |
| 전체 선택 회귀 | 98/98 PASS, Exit 0 |
| Workspace Lint | 11 files PASS, Exit 0 |
| Web Production Build | PASS, Exit 0; `/settings/account`, `/settings/organization` Static Route 생성 |
| 공통 Quality Gate | FAIL, Exit 1, Failures 1 |
| Gate PASS Category | lint·type·unit·contract·build·independence |
| Gate Security | static scan PASS; `production-dependency-audit`만 FAIL |

공통 Gate가 다시 확인한 설치 기준선은 `next@16.2.10` 경유 `sharp@0.34.5` High 및 `postcss@8.4.31` Moderate이다. Audit 집계는 High 2, Moderate 1, Critical 0이며 Next는 `fixAvailable=false`로 기록됐다.

## 생성·변경한 결과

- `packages/ui/src/account-security-model.js`
- `scripts/tests/account-security.test.mjs`
- `docs/01_architecture/account_security_prototype_adapter_contract.md`
- `docs/03_evidence/release_1/R1-M2-06/evidence-manifest.json`
- `docs/04_test_reports/release_1/R1-M2-06_progress.md`
- 이 결과보고

`package.json`, `package-lock.json`, Toolchain, CI는 변경하지 않았다. 공통 Gate가 생성한 범위 밖 R1-M1-05 결과 두 파일은 판독 후 HEAD로 복원했다. 기존 보호 Dirty인 R1-M1-04 `dependency-graph.json`, `violations.json`은 수정·복원·Stage하지 않았다.

## 미해결 사항과 조치

- 미해결: 기존 Production Dependency Audit 한 건.
- 필요한 판단: 어울1이 별도 Dependency Remediation 작업을 승인 경계에 맞게 구성하고, 보정 뒤 공통 Gate를 다시 실행해야 한다.
- Commit·Push·ysna-server 배포·PR·Merge는 수행하지 않았다.

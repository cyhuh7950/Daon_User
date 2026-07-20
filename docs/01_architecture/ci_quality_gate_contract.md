# Release 1 CI·품질·개발 통합 Gate 계약

## 목적

로컬과 GitHub CI는 `quality-gate-policy.json`과 `scripts/verify-quality-gate.mjs`를 함께 사용한다. CI 전용 완화 규칙은 없다. 병합 후보는 로컬 Gate, CI 고정 Job, 어울1이 Push한 불변 Git SHA의 ysna-server 격리 검증을 모두 통과해야 한다.

## 판정과 Exit Code

| 값 | 의미 |
| --- | --- |
| `PASS` | 필수 검사가 모두 성공했다. |
| `FAIL` | 품질·보안·독립성 위반 또는 필수 Capability/명령 누락이다. Process Exit는 `1`이다. |
| `NOT_APPLICABLE_FOUNDATION_ONLY` | Policy가 선언한 정확한 Runtime Source·설정 부재 조건을 모두 만족한 Source 전용 Capability다. |
| `ERROR` | Policy Schema, 명령 Spawn, Registry/Network Audit 등 실행 불능이다. Process Exit는 `2`다. |

단순 미구현, Script 누락, 명령 실패는 Foundation 예외로 바뀌지 않는다. Component에 Policy Signal 파일·확장자·Directory가 하나라도 생기면 연결된 명령이 필수이며, 명령이 없으면 `MISSING_REQUIRED_CAPABILITY`로 실패한다.

Policy Schema는 `foundation_status`, 승인된 8개 Component ID·Root·Manifest, 4개 상시 필수 Check ID·범주·종류와 모든 실행 명령을 fail-close로 검증한다. Component 또는 필수 Check의 삭제·중복·변형, 빈 명령, 존재하지 않거나 Component Root 밖의 Manifest는 검사 실행 전에 `ERROR/Exit 2`로 종료한다.

## 7개 범주

- `lint`, `type`, `unit`, `contract`, `build`는 Component별 Signal과 명령을 판정한다.
- Gate Runner Test는 `unit`, 정확 Toolchain·Workspace·Lockfile 검증은 `build`에서 항상 실행한다.
- `security`는 Secret 의심값·금지 Runtime 주소 정적 검사와 `npm audit --omit=dev --audit-level=high --json`을 항상 실행한다. Registry/Network 불능은 성공이 아니라 `ERROR/Exit 2`다.
- `independence`는 기존 `npm run verify:independence -- --no-write`를 항상 실행하고 위반 0건을 요구한다.

현재 Foundation Repository는 Component Manifest와 README만 승인되어 Source 전용 검사가 `NOT_APPLICABLE_FOUNDATION_ONLY`일 수 있다. 제품 Source나 Build/Contract 설정이 추가되는 Work Order는 같은 변경에서 해당 Component의 Policy Command를 활성화해야 한다.

## 로컬과 CI

로컬 기본 실행:

```text
npm ci
npm run verify:quality-gate
```

GitHub Workflow는 승인 Pin과 `package-lock.json`으로 `npm ci`를 수행한 뒤 같은 Root Script를 실행한다. 고정 Job ID는 `release-1-quality-gate`다. 결과 성공·실패와 무관하게 다음 파일을 Artifact로 보존한다.

CI는 `.node-version`의 Node를 설정한 뒤 `toolchain-versions.json`에서 npm·Corepack·uv Pin을 읽는다. 승인 npm·Corepack을 전역 설치하고 공식 `astral-sh/setup-uv` Action으로 승인 uv를 준비한 다음 실제 버전을 출력하고 `npm run verify:toolchain`으로 검증한다. 이 준비·검증은 `npm ci`와 공통 품질 Gate보다 먼저 실행하며 실패 완화 옵션을 사용하지 않는다.

- `docs/03_evidence/release_1/R1-M1-05/quality-gate-result.json`
- `docs/03_evidence/release_1/R1-M1-05/quality-gate-summary.md`

JSON은 Git SHA, Policy Hash, 범주별 명령·시작/종료·Exit·상태·근거 파일 Hash와 알려진 한계를 포함한다. 원시 stdout/stderr, Secret·Token·개인정보·내부 자격증명 값은 저장하지 않는다.

Checkout 직후 Ephemeral Runner에서 위 두 Evidence 파일을 제거해 저장소에 추적된 이전 PASS가 현재 실행 증거로 재사용되지 않게 한다. Toolchain 준비·검증, `npm ci` 또는 공통 Gate가 두 파일을 만들기 전에 실패하면 `always()` Fallback 단계가 현재 `github.sha`와 고정 Step ID별 GitHub Outcome만 담은 `ERROR/Exit 2` JSON·Summary를 생성한다. Fallback은 두 파일이 모두 존재하고 Result JSON을 안전하게 Parse한 뒤 현재 `github.sha`와 일치하며 최소 결과 계약이 유효할 때만 공통 Gate 결과를 보존한다. Parse 실패, SHA 불일치, 단일 파일 존재 또는 최소 계약 불일치이면 기존 두 파일을 제거하고 현재 SHA의 Fallback 두 파일을 재생성한다. Artifact Upload는 Fallback 뒤에 `always()`로 실행하므로 현재 실행에서 생성 또는 검증·보존된 두 파일만 업로드하며 Raw stdout/stderr와 자격증명 값은 기록하지 않는다.

## ysna-server Merge 전 계약

S5 이후 어울1이 Diff를 검토해 Commit·Push하고 전체 Git SHA를 전달한다. 어울2는 그 SHA만 `/home/ubuntu/deploy/daon-user/R1-M1-05/<full_git_sha>`에 격리 Checkout하며 Branch 최신 상태나 Local Dirty Source로 대신하지 않는다.

서버 검증은 SHA·ARM64·Toolchain·Lockfile·공통 Gate·독립성 위반 0건을 확인한다. 현재 Schema/Migration 경로가 없을 때만 기계 검사 근거와 함께 `NOT_APPLICABLE_NO_SCHEMA`를 기록한다. 검증 전후 기존 Docker Container·Network·Volume과 승인 Root 밖 상태의 변경이 0건이어야 한다. 기존 `shared-db`, `common`, `netdata`, `proxy`를 참조·변경·재시작·삭제하지 않고 Source-only 검증에 Listen Port를 열지 않는다.

CI 또는 필수 서버 검증이 실패하거나 서버 증거가 없으면 합격·Merge 대상으로 보고하지 않는다. Branch Protection 관리자 설정은 본 Work Order 범위 밖이며 어울1이 별도로 확인한다.

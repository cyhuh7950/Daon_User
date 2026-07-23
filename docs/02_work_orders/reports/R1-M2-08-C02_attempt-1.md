COMPLETED | R1-M2-08-I001 | C02 Legacy 고정·계보 복구 | Helper·공격 Test·Reconciliation·Manifest·Progress·본 보고서 | 전용 19/19·전체 186/186·Lint·Build·Gate PASS | Legacy Drift 4건은 승인 Observation으로 유지 | 어울1 재검토 요청

# R1-M2-08-C02 작업 결과보고

## 판정

`COMPLETED` — C02가 지정한 C2 Legacy 고정 계약과 C3 Origin 계보 형식 결함을 승인 범위 안에서 모두 복구했다. 제품 UI·CSS·Route·Contract·PNG와 선행 Manifest는 변경하지 않았다.

## 판단 이유

- 승인 Special Case 8건을 내부 불변 계약으로 고정했다. 이 중 Legacy 4건은 정확한 Work Order, Artifact 경로, SHA-256, Byte, 승인 Origin Commit이 모두 일치할 때만 `LEGACY_MANIFEST_DRIFT`를 반환한다.
- SHA·Byte·둘 다·Work Order·경로 변조와 Unknown Legacy, 외부 `lineage_verified:true` 주입은 모두 `UNEXPLAINED_MISMATCH`로 차단된다.
- Manifest 선언 Commit 문자열과 Origin Blob 표현의 변수 역할을 분리해 일반 82건 계보 필드의 객체 직렬화를 제거했다.
- Origin은 원 Manifest의 실제 문자열을 보존한다. 정본 R1-M2-03의 7자 단축 SHA `682cd3c`도 보존하되, `null` 또는 7~40자 Hex이고 실제 Commit이 존재하는 경우만 허용한다. 빈 문자열·비hex·길이 오류·객체·배열·미존재 Commit은 fail-close한다.
- Reconciliation은 90건, `DIRECT_MATCH=82`, `SUCCESSOR_SUPERSEDED=4`, `LEGACY_MANIFEST_DRIFT=4`, `UNEXPLAINED_MISMATCH=0`이다. Legacy schema bytes 정규화 24건과 Verified Origin Blob 2건을 유지했다.

## 조치

### 변경 파일

- `scripts/lib/predecessor-evidence-reconciliation.mjs`
- `scripts/tests/platform-prototype-evidence.test.mjs`
- `docs/03_evidence/release_1/R1-M2-08/predecessor-evidence-reconciliation.json`
- `docs/03_evidence/release_1/R1-M2-08/evidence-manifest.json`
- `docs/04_test_reports/release_1/R1-M2-08_progress.md`
- `docs/02_work_orders/reports/R1-M2-08-C02_attempt-1.md`

### TDD 및 검증

- 최초 C02 계약 RED: 승인 Special Case Export 부재로 `0 PASS / 1 FAIL`.
- Origin 경계 추가 RED: 검증 Export 부재로 `0 PASS / 1 FAIL`.
- 전용 최종: `19/19 PASS`.
- 전체 순차 회귀: `186/186 PASS`.
- Workspace Lint: `11 files PASS`.
- Web Production Build: Compile·TypeScript·정적 페이지 `7/7`, Route 7개 PASS.
- 공통 Quality Gate: 7개 Category PASS, Failure 0.

### 환경 복구 기록

- Windows 샌드박스가 Node의 `.next/trace-build`, `.next/trace`, `.next` mkdir을 EPERM 처리했다. 장시간 대기와 Process/ACL/Open 진단 후 기존 `.next`를 정확한 동일 Worktree 내부 격리 경로로 가역 이동했다.
- 빈 `.next`에서도 샌드박스 EPERM이 재현되어 동일 Build 명령만 샌드박스 밖 파일 쓰기로 실행했고 PASS했다. 소스·설정·권한은 변경하지 않았다.
- 격리 캐시가 첫 Gate에서 Runtime Source로 스캔되어 7건을 발생시킨 사실을 확인했다. 새 Build 증거를 유지하고 재생성 가능한 격리 캐시 1,818개 항목만 승인된 정확 경로에서 정리한 뒤 Gate를 재실행해 PASS했다.
- Reconciliation Node 직접 쓰기도 EPERM이어서 Helper 출력 전체를 Workspace 내부 고유 파일로 기록했다. 교체 전 96,457 UTF-8 bytes, JSON Parse, 90건·82/4/4/0·Legacy 24·Verified Origin 2·잘못된 Origin 0을 확인하고 대상에 1회 교체했다.

### 미해결 위험과 다음 판단

- Legacy Drift 4건은 승인된 과거 Observation이며 일반 선행 Hash 완전성 PASS로 세지 않는다.
- Commit·Push·PR·Merge·배포·TP-1 판정은 수행하지 않았다.
- 어울1은 최신 Diff와 이 보고서를 독립 재검토하고 다음 진행 여부를 판단해야 한다.

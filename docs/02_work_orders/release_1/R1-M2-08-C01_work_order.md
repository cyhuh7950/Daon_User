# R1-M2-08-C01 수정 작업지시서 — 선행 Evidence 판정 Fail-close

## 1. 판정과 범위

| 항목 | 내용 |
| --- | --- |
| 원 작업 | `R1-M2-08` |
| 수정 작업 | `R1-M2-08-C01` |
| issue_id | `R1-M2-08-I001` 유지 |
| 판정 | 독립 검토 `REJECT(C2)` — 선행 Evidence 변조 판정 Fail-open |
| 작업자 | 동일 어울2 · Project Custom Agent `daon-developer` |
| 작업 위치 | `C:\tmp\Daon_User-r1-m2-08` · `codex/r1-m2-08` |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M2-08_progress.md`에 C01 구간 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-08-C01_attempt-1.md` |

원 작업지시서, C00 Addendum, 현재 구현과 독립 검토 결과를 정본으로 사용한다. 기존 Evidence Hub UI·플랫폼 계약·22개 M2-08 증거는 수정 사유가 없는 한 다시 열지 않는다.

## 2. 결함

1. 알려진 `specialCases` 8건이 아니면 실제 Hash·Byte 비교 결과와 무관하게 `DIRECT_MATCH`가 된다.
2. Raw가 다를 때 Git Canonical 표현을 선택하지만 Git Blob Hash·Byte가 Manifest 기대값과 같은지 검사하지 않는다.
3. 완료 판정이 `UNEXPLAINED_MISMATCH` 누락·`null`·비숫자 값을 0처럼 취급한다.
4. 실제 Manifest 변조를 입력한 공격 테스트 없이 완성된 Summary 숫자만 바꾸어 차단을 확인한다.

## 3. 필수 보정 계약

### 3.1 Artifact 분류

각 Artifact는 아래 순서와 조건으로만 분류한다.

- Manifest의 `sha256`과 `bytes`가 유효한 형식이어야 한다. 유효하지 않으면 `UNEXPLAINED_MISMATCH`다.
- Raw Hash와 Raw Byte가 기대 Hash·Byte 모두와 일치하면 `DIRECT_MATCH` + `RAW`다.
- Raw가 불일치해도 Git Canonical Hash와 Git Canonical Byte가 기대 Hash·Byte 모두와 일치하면 `DIRECT_MATCH` + `GIT_CANONICAL`이다.
- C00에 명시되고 계보 Commit을 실제로 확인할 수 있는 4건만 `SUCCESSOR_SUPERSEDED`다.
- C00에 명시한 Legacy 4건만 `LEGACY_MANIFEST_DRIFT`다. 목록 확장을 금지한다.
- 위 조건에 들지 않는 존재 오류·읽기 오류·Hash 불일치·Byte 불일치·계보 불명은 모두 `UNEXPLAINED_MISMATCH`다.
- Special Case라는 이유만으로 실제 경로·기대값·계보 검증을 생략하지 않는다.

### 3.2 Summary와 완료 판정

- Summary의 `artifact_count`, 네 상태 Count, `predecessor_status`가 모두 존재해야 한다.
- Count는 유한한 0 이상의 정수여야 한다.
- 네 상태 Count의 합이 `artifact_count`와 정확히 같아야 한다.
- 현재 승인 기준선은 Artifact 90건과 `82/4/4/0`이다. 이 기준선과 다르면 자동 완료하지 않고 `blocked`로 닫는다.
- 누락, `null`, 문자열, NaN, 음수, 소수, 합계 불일치, `UNEXPLAINED_MISMATCH > 0`은 모두 `completable:false`다.
- 차단 Code는 원인을 기계 판독할 수 있게 안정적으로 반환한다. 기존 설명되지 않은 불일치 Code는 보존한다.

### 3.3 공격 테스트

RED를 먼저 확인하고 다음을 GREEN으로 만든다.

- 알려지지 않은 일반 Artifact의 Hash 변조
- Byte만 불일치
- Raw 불일치이며 Git Canonical도 기대값과 불일치
- 파일/Canonical Blob 부재
- Summary `{}`와 필수 필드 누락
- `null`, 문자열, NaN, 음수, 소수
- Count 합계 불일치와 Artifact Count 90 불일치
- 승인된 `82/4/4/0`만 완료 가능
- 알려진 8건의 분류와 현재 90건 Reconciliation은 회귀 없이 유지

테스트 전용 임시 입력은 저장소 추적 Artifact를 직접 변조하지 않고 주입 가능한 순수 분류 함수·임시 디렉터리·Fixture를 사용한다.

## 4. 허용 변경

- `scripts/tests/platform-prototype-evidence.test.mjs`
- 필요 시 순수 검증 Helper 1개 (`scripts/lib/` 또는 동등한 최소 위치)
- `packages/ui/src/production-bound-evidence-model.js`의 완료 판정 함수
- 생성 결과인 `docs/03_evidence/release_1/R1-M2-08/predecessor-evidence-reconciliation.json`
- M2-08 Evidence Manifest(변경된 Artifact Hash·Byte만 갱신)
- 기존 Progress, C01 결과보고

금지: UI/CSS/Route/Contract/Dependency/Lockfile/Toolchain 변경, 승인된 Legacy/Superseded 목록 확대, 테스트 기대값에 맞춘 변조 은폐.

## 5. 검증과 완료조건

1. 공격 테스트 RED 증거를 Progress에 기록한다.
2. C01 전용 테스트와 기존 M2-08 전용 테스트가 모두 PASS한다.
3. 전체 순차 회귀 178개 이상, Lint, Production Build, Quality Gate가 PASS한다.
4. 재생성 Reconciliation이 실제 90건·82/4/4/0이며 각 일반 Artifact가 Hash와 Byte로 분류됐음을 확인한다.
5. M2-08 Evidence Manifest의 모든 Artifact Hash·Byte를 재검증한다.
6. 관련 없는 Diff가 0이며 Browser 화면 의미와 7개 PNG는 변경하지 않는다.
7. 결과보고는 `판정 → 판단 이유 → 조치` 순서이며 첫 줄은 아래 형식이다.

```text
COMPLETED | R1-M2-08-I001 | C01 fail-close 보정 | 변경 파일 | 테스트 근거 | 미해결 위험 | 어울1 재검토 요청
```

Commit·Push·PR·Merge·서버 배포는 수행하지 말고 종료 즉시 쓰기를 중지한다.

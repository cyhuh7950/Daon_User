# R1-M2-08-C02 수정 작업지시서 — Legacy 기대값 고정과 계보 필드 복구

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| 원 작업 | `R1-M2-08` |
| 수정 작업 | `R1-M2-08-C02` |
| issue_id | `R1-M2-08-I001` 유지 |
| 판정 | C01 독립 재검토 `REJECT`: C2 Legacy 변조 흡수, C3 Origin 계보 필드 형식 손상 |
| 작업자 | 동일 어울2 · `daon-developer` |
| 위치 | `C:\tmp\Daon_User-r1-m2-08` · `codex/r1-m2-08` |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M2-08_progress.md`에 C02 구간 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-08-C02_attempt-1.md` |

원 Work Order, C00, C01과 독립 검토 결과를 EOF까지 읽는다. 기존 UI·CSS·Route·Contract·PNG와 통과한 일반 Artifact·Summary 검증은 다시 열지 않는다.

## 2. C2 필수 보정 — Legacy 4건 기대값 고정

- C00에서 승인된 `LEGACY_MANIFEST_DRIFT` 정확히 4개 `(source_work_order, artifact_path)`에 대해 승인 기준선의 정확한 `expected_sha256`과 `expected_bytes`를 코드의 불변 특별항목 계약에 명시한다.
- 기준값은 현재 승인된 선행 Manifest와 C01 Reconciliation에서 대조해 기록하고 Progress에 4개 경로·Hash·Byte 출처를 남긴다. 원 선행 Manifest는 수정하지 않는다.
- 입력 Manifest의 경로·SHA·Byte가 불변 특별항목 계약과 모두 일치해야만 Legacy 검증을 시작한다. 하나라도 다르면 `UNEXPLAINED_MISMATCH`다.
- Legacy 검증은 승인 Origin Commit 존재, 정확한 경로, 승인 기대값 고정, Origin/final 표현이 승인 기대값과 일치하지 않는다는 기존 Drift 사실을 함께 검증한다.
- 단순히 `originExists && !originMatches`이거나 호출자가 `lineage_verified:true`를 주었다는 이유만으로 Legacy를 수락하지 않는다.
- 승인 목록 확대와 동적 기대값 학습을 금지한다.

공격 테스트:

- Legacy 4개 경로 각각의 SHA-only 변조
- Byte-only 변조
- SHA+Byte 동시 변조
- 경로/Work Order 바꿔치기
- 알려지지 않은 Legacy와 외부 `lineage_verified:true` 주입
- 정확한 승인 기대값·경로·계보만 기존 4건으로 분류

## 3. C3 필수 보정 — Origin 계보 필드

- Manifest가 선언한 원 구현/증거 Commit 문자열과 Origin Blob 표현 객체의 변수명·용도를 분리한다.
- 모든 90건의 `origin_implementation_or_evidence_commit`는 `null` 또는 Git Commit SHA 문자열이어야 하며 객체·배열을 금지한다.
- 일반 `DIRECT_MATCH` 82건은 각 원 Manifest가 선언한 `validated_head | source_commit | head_sha | implementation_sha`의 실제 문자열을 보존한다.
- Special Case는 승인된 `origin_commit` 문자열을 보존한다.
- SHA 문자열은 Commit 존재 여부를 별도 표시하거나 검증하되, 내부 표현 객체를 계보 필드에 직렬화하지 않는다.

테스트:

- 90개 계보 필드의 형식 검사
- 일반 82건이 해당 Manifest 선언 Commit과 정확히 일치
- Special 8건이 승인 Origin Commit과 정확히 일치
- 객체·빈 문자열·잘못된 SHA 차단

## 4. 허용 범위와 완료조건

허용:

- `scripts/lib/predecessor-evidence-reconciliation.mjs`
- `scripts/tests/platform-prototype-evidence.test.mjs`
- 재생성 Reconciliation·M2-08 Manifest
- 기존 Progress·C02 결과보고

필요하지 않으면 Product Model을 수정하지 않는다. UI·CSS·Route·Contract·Dependency·Lockfile·Toolchain·선행 Manifest 수정은 금지한다.

완료조건:

1. 추가 공격 테스트의 유효 RED→GREEN 기록.
2. 전용 테스트, 전체 순차 회귀 183개 이상, Lint, Web Production Build, Quality Gate PASS.
3. Reconciliation 90건·82/4/4/0, Legacy 4건 기대값 고정, Legacy bytes 정규화 24, Verified Origin 2 유지.
4. 90개 Origin 계보 필드가 문자열/null 계약을 만족하고 일반 82건·Special 8건 값이 출처와 일치.
5. M2-08 Manifest 전수 Hash·Byte PASS, 관련 없는 Diff·임시 파일·실행 포트 0.
6. 결과보고은 `판정 → 판단 이유 → 조치` 순서로 작성하고 첫 줄은 다음과 같다.

```text
COMPLETED | R1-M2-08-I001 | C02 Legacy 고정·계보 복구 | 변경 파일 | 테스트 근거 | 미해결 위험 | 어울1 재검토 요청
```

Commit·Push·PR·Merge·배포는 수행하지 않고 제출 후 쓰기를 중지한다.

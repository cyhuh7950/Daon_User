# R1-M2-08-C00 기술 보정 — 선행 Evidence Manifest 계보 정합

## 1. 판정

`BLOCKED_PENDING_TECHNICAL_DECISION`을 해소한다. 과거 Manifest를 현재 Working Tree와만 비교한 90/90 일치 요구는 후속 Work Order가 공유 파일을 정상 승계한 이력을 표현하지 못한다. 과거 Manifest를 수정하지 않고 Artifact 시점과 후속 계보를 분리해 검증한다.

이 보정은 제품 기능·요구사항·공개 API·데이터·보안 경계를 바꾸지 않는다. 과거 증거 결함을 PASS로 위장하거나 예외 수용하지 않고 M2-08 및 TP-1 Observation으로 공개한다.

## 2. 확인된 기준선

- 선행 Manifest Artifact 90건 중 82건은 Raw 또는 Git Canonical Blob 기준으로 직접 일치한다.
- 다음 4건은 원 Manifest 시점 Blob이 존재하고 후속 Commit이 공유 파일을 변경했다.
  - M2-04 결과보고 → 후속 Evidence Commit `da99baf`
  - M2-05 Progress → 후속 Evidence Commit `c42e1d`
  - M2-06 `packages/ui/src/index.js`, `workspace.css` → M2-07 구현 Commit `6fdcfa2`
- 다음 4건은 Manifest 기대 Blob이 Git History에 없어 `LEGACY_MANIFEST_DRIFT`다.
  - M2-06 `toolchain-versions.json`, `docs/01_architecture/DECISIONS.md`
  - M2-07 `packages/ui/src/index.js`, `packages/ui/src/workspace.css`

## 3. 보정된 검증 계약

`docs/03_evidence/release_1/R1-M2-08/predecessor-evidence-reconciliation.json`에 90건을 다음 상태로 기록한다.

| 상태 | 판정 기준 | 합격 계산 |
| --- | --- | --- |
| `DIRECT_MATCH` | Manifest Hash/Byte가 해당 Artifact 시점의 Raw 또는 명시된 Git Canonical Blob과 일치 | 일치로 계산 |
| `SUCCESSOR_SUPERSEDED` | 원 기대 Blob이 History에 존재하고 후속 Commit·현재 Blob까지 계보가 설명됨 | 계보 검증으로 계산; 현재 Hash 일치로 표현 금지 |
| `LEGACY_MANIFEST_DRIFT` | 기대 Blob이 History에 없거나 동일 Commit의 최종 Blob과 불일치 | PASS로 계산 금지; Observation과 영향 기록 |
| `UNEXPLAINED_MISMATCH` | 원인·시점·후속 계보를 증명하지 못함 | M2-08 `COMPLETED` 금지 |

필수 필드: 원 Work Order, Manifest 경로, Artifact 경로, Expected Hash/Byte, Raw Hash/Byte, Git Blob Hash/Byte, 원 구현/증거 Commit, 후속 Commit, 상태, 근거, 현재 M2-08 영향.

## 4. 완료 조건 보정

- 과거 Manifest를 수정·재생성하지 않는다.
- `UNEXPLAINED_MISMATCH=0`이어야 한다.
- 현재 M2-08이 직접 사용하는 Route·Model·Pane·Contract는 현재 HEAD에서 Hash·Build·전체 회귀로 별도 검증한다.
- `LEGACY_MANIFEST_DRIFT` 4건은 수량·경로·영향을 결과보고와 TP-1 기술 의견서에 명시한다.
- 이 4건은 현재 기능·보안 실패로 확대 해석하지 않되, 원 증거의 Hash 완전성 PASS로도 표현하지 않는다.
- Evidence Hub의 선행 상태는 `verified_with_observations`로 표시하고 상세 Reconciliation으로 연결한다.

원 R1-M2-08 작업지시서의 “Manifest Artifact가 다르면 완료 금지”는 이 Addendum의 상태 분류를 적용한다. 설명되지 않은 불일치만 완료를 차단하고, 설명된 후속 승계와 공개된 레거시 드리프트는 위 계약을 충족하면 다음 단계로 진행한다.

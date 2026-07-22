COMPLETED | R1-M2-08-I001 | 어울1 직접 구현으로 Legacy 특별 경로의 현재 표현 우회 차단 | Helper·공격 Test·Manifest·Progress | RED 18/19→GREEN 19/19·전체 186/186·Lint·Build·Gate PASS | Legacy Drift 4건 TP-1 Observation 유지 | 최신 Diff 독립 재검토 요청

# R1-M2-08 어울1 직접 구현 결과보고

## 판정

`DIRECT_IMPLEMENTATION COMPLETED` — 동일 issue의 세 번째 독립 미완료 판정 후 신산님 승인을 받아 어울1이 단일 Writer로 인수했다. 승인 Legacy 특별 경로의 고정 기대값 검사를 일반 Raw/Git 직접 일치보다 먼저 수행하도록 판정 순서만 최소 수정했다.

## 판단 이유

- 기존 구현은 승인 Legacy `(Work Order, Path)`를 찾더라도 먼저 Raw/Git 표현과 Manifest 기대값을 비교했다.
- 공격자가 Legacy Manifest 기대값을 현재 Raw 또는 Git Canonical Hash·Byte로 바꾸면 승인 고정값과 다르지만 `DIRECT_MATCH`로 승격될 수 있었다.
- 승인 Legacy 경로는 고정 SHA·Byte와 다르면 현재 표현과 일치하더라도 즉시 `UNEXPLAINED_MISMATCH / LEGACY_EXPECTATION_MISMATCH`로 닫혀야 한다.

## 조치

- Legacy 4건 각각에 `expected=current Raw`와 `expected=current Git Canonical` 공격 단언을 추가했다.
- Product Helper 수정 전 동일 전용 Test를 실행해 `18/19 PASS · 1 FAIL`과 `DIRECT_MATCH/RAW_HASH_AND_BYTES_MATCH` 우회를 확인했다.
- `classifyPredecessorArtifact`에서 승인 Special Case 조회와 Legacy 고정 기대값 검사를 Raw/Git 직접 일치보다 앞으로 이동했다.
- 동일 전용 Test `19/19`, 전체 순차 회귀 `186/186`, Workspace Lint 11 files, Web Production Build 7 routes, Quality Gate 7 categories·failures 0을 확인했다.
- 샌드박스 안 Web Build는 기존과 같은 `.next/trace` 쓰기 `EPERM`이었고, 동일 명령만 샌드박스 밖에서 실행해 Compile·TypeScript·정적 페이지 7/7을 확인했다.
- Gate가 생성한 범위 밖 R1-M1-05 Evidence 2개는 HEAD로 복원했다.

Commit·Push·PR·Merge·ysna-server 검증·TP-1 판정은 이 보고 시점에 수행하지 않았다.

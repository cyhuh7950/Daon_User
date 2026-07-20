# Release 1 Git 개발 기준선

## 문서 목적과 적용 범위

이 문서는 승인된 `R1-M1-01` 범위에서 Release 1의 Branch·Commit·PR·보호 기준을 고정한다. 원격 보호 설정을 변경하거나 Commit·Push를 수행하지 않는다.

## 기준 Commit 계보

| 구분 | Commit | 관계 |
| --- | --- | --- |
| 문서 기준선 | `c94e553f3a6aa7d062645391e838e7a555706914` | G0와 작업 패킷 기준선의 조상 |
| G0 승인 | `3397b57882d0e9580bc2561403d07bee65396d92` | 문서 기준선의 후속 Commit |
| 작업 패킷 기준 | `dbb9aa2ff5c40dec9c9a711cc39643580c67f08f` | G0 승인 Commit의 후속 Commit |
| R1-M1-01 착수 HEAD | `9a2c9716871576b67799e093fb87be63531c68be` | 작업 패킷 기준의 후속 Commit |

실제 `git merge-base --is-ancestor` 검사에서 문서 기준선, G0 승인 Commit, 작업 패킷 기준 Commit은 모두 착수 HEAD의 조상이다.

## Branch 기준

| Branch | 책임 | 생성·승계 기준 | 원격 상태 |
| --- | --- | --- | --- |
| `master` | 승인·Release 기준 Branch | 검증·승인된 변경만 PR로 병합 | 원격 `master`가 `9a2c9716871576b67799e093fb87be63531c68be`를 가리킴을 읽기 전용으로 확인 |
| `codex/release-1` | Release 1 통합 개발 Branch | 착수 HEAD `9a2c971...`에서 로컬 생성, `dbb9aa2...`를 조상으로 승계 | 로컬만 생성. Push 금지 범위이므로 원격 Branch는 미생성 |
| `codex/<work-order-id-lowercase>` | 후속 Work Order 전용 Branch | 원칙적으로 `codex/release-1`의 검증된 최신 기준에서 분기 | Work Order별 한 Writer·한 변경 범위 유지 |

후속 Work Order Branch 예시는 `R1-M1-02`에 대해 `codex/r1-m1-02`다. 동일 Work Order 범위를 여러 Writer가 병렬 수정하지 않는다.

## Commit 기준

1. Commit은 Work Order 단위로 검토 가능한 크기를 유지한다.
2. Commit 메시지는 Work Order의 목적을 식별할 수 있게 작성한다.
3. 개발 Subagent 어울2는 작업 결과와 검증 근거를 제출하되 Commit·Push하지 않는다.
4. 어울1이 Diff·테스트·Evidence Manifest를 검토한 뒤 Commit·Push를 수행한다.
5. 무관 파일을 함께 Stage하거나 기존 Dirty·Untracked 파일을 정리·되돌리지 않는다.

## PR과 병합 기준

1. Work Order Branch에서 `codex/release-1`로 PR을 생성한다.
2. Release 통합 검증이 끝난 `codex/release-1`에서 `master`로 PR을 생성한다.
3. 직접 Push와 검증 전 병합을 금지한다.
4. 필수 CI가 모두 통과하고 미해결 검토 의견이 0건일 때만 병합한다.
5. 승인된 Work Order 범위, 변경 파일, 테스트, Evidence Manifest와 미해결 위험을 PR에 연결한다.
6. 강제 Push와 보호 Branch 삭제를 금지한다.

## 목표 보호 규칙과 실제 확인 수준

`master`와 `codex/release-1`의 목표 보호 규칙은 다음과 같다.

- 직접 Push 금지
- PR 기반 병합
- 필수 CI 통과
- 미해결 검토 의견 0건
- 강제 Push 금지
- Branch 삭제 금지

| 확인 대상 | 상태 | 근거·제한 |
| --- | --- | --- |
| 원격 Repository·`master` Branch | `VERIFIED` | `git remote -v`, `git ls-remote --heads origin`; 원격 `master`=`9a2c971...` |
| 원격 `codex/release-1` | `VERIFIED_NOT_PRESENT` | 원격 Heads 조회에 없음. 이번 Work Order는 Push 금지 |
| GitHub `master` 보호 규칙 | `NOT_VERIFIED` | Git 원격 조회는 보호 규칙을 제공하지 않음. GitHub API 권한 조회 결과를 별도 기록 |
| GitHub `codex/release-1` 보호 규칙 | `NOT_VERIFIED` | 원격 Branch 자체가 아직 없으며 보호 설정 변경은 제외 범위 |

`NOT_VERIFIED`는 보호가 적용됐다는 뜻이 아니다. 어울1은 Push 이후 GitHub 관리 권한으로 두 Branch의 Branch protection 또는 Ruleset을 조회하고, 위 목표 규칙과 일치하는지 확인해야 한다. 설정 변경이 필요하면 별도 승인 경계에서 수행한다.

## 기존 파일 보존과 회귀 방지

- 착수 시 Worktree는 clean이었다.
- 착수 HEAD의 추적 파일은 43개였다.
- 작업 패킷 기준 Commit 이후 착수 HEAD까지 추가 2개, 수정 1개, 삭제 0개였다.
- 이번 작업은 허용된 문서·진행 기록·결과보고·Evidence Manifest와 로컬 Branch 참조만 변경한다.
- 애플리케이션·패키지·설정·승인 정본은 수정하지 않는다.
- 완료 시 `git diff --check`, 추적 파일 삭제 0건, 허용 경로 밖 Diff 0건을 다시 확인한다.

## 현재 제한과 후속 확인

- 로컬 `codex/release-1`은 착수 HEAD에서 생성됐지만 Commit·Push되지 않았다.
- GitHub 보호 규칙의 실제 적용 여부는 원격 Git 조회만으로 판정하지 않는다.
- 어울1 검토 후 Commit·Push가 이루어진 다음 원격 Branch 존재와 보호 규칙을 다시 확인해야 한다.

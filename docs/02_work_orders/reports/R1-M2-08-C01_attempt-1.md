COMPLETED | R1-M2-08-I001 | C01 fail-close 보정 | 순수 Helper·전용 Test·완료 판정 Model·Reconciliation·Manifest·Progress·보고 | 전용 16/16·전체 183/183·Lint·Build·Gate PASS·Manifest 90건 82/4/4/0 | Legacy Drift 4건은 기존 TP-1 Observation 유지 | 어울1 재검토 요청

# R1-M2-08-C01 수정 작업 완료 보고

## 판정

`COMPLETED` — 독립 검토 `REJECT(C2)`의 네 가지 Fail-open을 실제 입력 기반 공격 테스트와 순수 검증 Helper로 보정했다. 설명되지 않은 불일치, 잘못된 Manifest 기대값, 계보 미검증과 잘못된 Summary는 모두 자동 완료되지 않는다.

## 판단 이유

- 일반 Artifact는 Manifest SHA-256과 Byte가 Raw 또는 HEAD Git Canonical 표현에 모두 일치할 때만 `DIRECT_MATCH`다. Hash 단독 일치, Byte 단독 불일치, 파일/Blob 부재와 Raw/Canonical 동시 불일치는 `UNEXPLAINED_MISMATCH`로 닫힌다.
- C00 승인 8건의 경로 목록은 확대하지 않았다. `SUCCESSOR_SUPERSEDED`는 Origin Commit Blob의 Manifest SHA·Byte, Successor Commit·대상 Blob, Origin→Successor Ancestor를 실제 확인한다. `LEGACY_MANIFEST_DRIFT`도 승인 Origin Commit의 존재와 기대 Blob 불일치를 확인한다.
- M2-02/04/05 구 `files` Schema에서 `bytes` 필드가 부재한 일반 DIRECT 24건만 Manifest SHA와 일치한 Raw/Git 표현의 실제 길이를 `LEGACY_SHA_MATCHED_REPRESENTATION`으로 정규화했다. 승인 Successor 2건은 별도 `VERIFIED_ORIGIN_BLOB`으로 분리했다. 명시 `null`, 잘못된 형식, SHA 불일치와 미검증 표현은 정규화하지 않는다.
- Summary의 6개 필수 필드, 비음수 정수, 네 상태 Count 합, Artifact 90건, 승인 `82/4/4/0`, `verified_with_observations`를 모두 확인한다. 누락·null·문자열·NaN·음수·소수·합계 또는 기준선 불일치는 안정 Code와 함께 `blocked`다.
- TDD 증거는 최초 Helper 부재 RED `0/1`, 실제 엄격 분류 RED `14/15`(`58/2/4/26`), legacy 경계 RED `15/16`(`26 !== 24`)을 거쳐 최종 전용 `16/16 PASS`다.
- 재생성 Reconciliation은 90건, `DIRECT 82 / SUCCESSOR 4 / LEGACY DRIFT 4 / UNEXPLAINED 0`, legacy 정규화 24, verified origin 2다.
- 전체 순차 회귀 `183/183`, Workspace Lint 11 files, Web Production Build 7 routes, Quality Gate 7 categories·failures 0이다.

## 조치

- 변경: `scripts/lib/predecessor-evidence-reconciliation.mjs`, `scripts/tests/platform-prototype-evidence.test.mjs`, `packages/ui/src/production-bound-evidence-model.js`, `predecessor-evidence-reconciliation.json`, M2-08 `evidence-manifest.json`, Progress와 본 보고.
- 미변경: Evidence Hub UI·CSS·Route·Handoff Contract·Dependency·Lockfile·Toolchain·Navigation/Screen 정본·Browser Evidence·PNG 7개. 실제 외부 효과 0건.
- 대상 Reconciliation의 Node 직접 쓰기는 Windows 파일 잠금으로 2회 `EPERM`이었고, 별도 생성 파일을 검증 후 PowerShell로 안전 교체했다. 이는 정식 실패보고가 아닌 환경 복구 이력이다.
- Commit·Push·PR·Merge·서버 배포·TP-1 판정은 수행하지 않았다. 어울1이 C01 최신 Diff와 Manifest Hash·Byte를 독립 재검토해야 한다.

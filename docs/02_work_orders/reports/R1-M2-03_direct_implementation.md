# R1-M2-03 어울1 직접 구현 보고

## 판정

`LOCAL_COMPLETED` · 동일 작업 `INCOMPLETE 3/3` 이후 신산님 승인으로 어울1이 인수한 세 C2 결함을 TDD로 수정하고 로컬 자동·Build·실제 Browser 검증을 완료했다. GitHub CI와 ysna-server ARM64 S10은 최종 불변 SHA Push 후 수행한다.

## 판단 이유

- Source Seed와 Domain 상태를 `projectSourceState`로 단일 투영해 목록·상세 상태 불일치를 제거했다.
- Parser/OCR 불일치는 `needs_review`로 정정하고, 별도 실제 `failed`와 `expired`에 재처리·재등록 진입을 명시했다.
- 충돌 심각도 상향과 해결을 Domain 전이로 연결하고 `ConflictPolicyVersion`·검토자·행동·해결 결과를 Audit Preview에 표시했다.
- 1차 RED 3건과 상태 조합 2차 RED 2건이 각각 정확한 결함으로 실패한 뒤 GREEN 20/20, Workspace 34/34, Lint 11, Foundation 8/8, Toolchain, Independence, Production Build, 공통 7범주 Gate를 통과했다.
- 실제 Browser에서 목록·상세 상태 동기화, Parser/OCR 불일치, failed·expired 복구 진입, 사용 중지, 해결 후 심각도 상향·검토 재개 흐름과 Console warning/error 0건을 확인했다.

## 변경 범위

- `packages/ui/src/source-knowledge-model.js`
- `packages/ui/src/source-knowledge-pane.jsx`
- `scripts/tests/source-knowledge.test.mjs`
- 본 직접 구현 보고, TDD 증거와 진행 기록

개발 Subagent가 만든 기존 R1-M2-03 구현·Architecture·Evidence 산출물은 보존했다. 새 Dependency·Lockfile·공개 API·DB·보안 경계 변경은 없다. R1-M1-04 보호 Dirty 두 파일은 수정·복원·Stage하지 않았다.

## 테스트 결과

| 구분 | 결과 |
| --- | --- |
| Source Test | 20/20 PASS |
| Workspace 통합 | 34/34 PASS |
| Workspace Lint | 11 files PASS |
| Product Foundation | 8/8 PASS |
| Toolchain | PASS |
| Independence | components 8 · edges 10 · violations 0 |
| Next Production Build | PASS · Exit 0 |
| Common Quality Gate | 7범주 PASS · Failures 0 · Exit 0 |
| 실제 Browser | 최종 상태 조합 포함 5개 흐름 PASS · Console warning/error 0 |

## 조치

- 로컬 판정: `LOCAL_COMPLETED`.
- 다음 단계: 최종 Diff 독립 재검토 → 남은 R1-M2-03 산출물 Commit·Push → GitHub CI·ysna-server ARM64 S10 검증.
- TP-1은 R1-M2-08 이후이므로 현재 사용자 Test Gate 도달 아님.

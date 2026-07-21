# 수정 작업지시서 R1-M2-03-C02 · 800px 증거 재촬영

## 0. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M2-03-I001` |
| Attempt 판정 | 동일 작업 누적 `INCOMPLETE 2/3` |
| 유효 FAILURE_REPORT | `0회` |
| 실행 | 동일 어울2 · 단독 Evidence 작성자 |
| 기준 | `codex/r1-m2-03` · HEAD `f390c19e101817d0f7e2785c01444521b642fb9e` · 현재 미커밋 C01 상태 |

## 1. 단일 목표

C01 코드·Test·Gate 결과는 다시 열지 않는다. Production Build를 사용해 800×900 `single-pane` 충돌 화면 PNG 한 건만 실제 DOM Paint 완료 뒤 재촬영하고, R1-M2-03 Browser JSON·Evidence Manifest·Attempt 2 보고를 최종 정합한다.

## 2. 실행 계약

1. C01 진행 기록과 현재 Diff를 확인하고 `C02_EVIDENCE_RECAPTURE` 착수를 기록한다.
2. 코드·Test·설정·Architecture를 수정하지 않는다. C01 최종 Build와 Production Server만 사용한다.
3. 800×900에서 자료·지식 Pane, `충돌` 탭, `single-pane`, informational/material/critical, 미해결 중요 충돌의 최종화 3종 disabled 상태를 실제 클릭으로 만든다.
4. DOM의 Layout Mode·선택 탭·충돌 3종·disabled 3건을 읽어 확인한 뒤 Browser-side Paint 완료를 충분히 기다린다.
5. `source-conflict-800x900.png` 한 건만 교체하고 원본 Pixel `800×900`과 시각 내용을 직접 확인한다.
6. `browser-validation.json`을 C01 최종 16/16·30/30 계약과 새 화면 결과로 전면 갱신한다. 다른 세 PNG도 현재 최종 파일을 기준으로 정확히 기록한다.
7. `evidence-manifest.json`의 모든 Hash를 실제 최종 파일 기준으로 다시 계산하고 전수 일치를 검증한다.
8. 필요한 좁은 회귀는 Source 16/16·Workspace 30/30·Lint 11만 재실행한다. 이미 통과한 C5 Common Gate·Build를 증거 재촬영 때문에 반복하지 않는다.
9. `git diff --check`, 추적 삭제 0, Lockfile Diff 0, 보호 R1-M1-04 두 파일 내용 Diff 0, Port/Process 정리를 확인한다.
10. `docs/02_work_orders/reports/R1-M2-03_attempt-2.md`를 완성하고 `HANDOFF_READY`로 종료한다.

## 3. 금지

- 코드·Test·설정·Architecture 수정
- 1920·1200·500 PNG의 불필요한 재촬영
- 개발 서버 사용, API·DB·LLM 연결, 새 Dependency·Lockfile 변경
- R1-M1-04 보호 파일 수정·복원·Stage
- Commit·Push·PR

진행 기록은 기존 `docs/04_test_reports/release_1/R1-M2-03_progress.md`에 이어 쓰고, 종료 후 모든 쓰기를 중지한다.

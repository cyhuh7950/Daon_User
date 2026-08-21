# R1-M8-10-SOURCE-PAGE-EVIDENCE-I006 진행 기록

- 작업 ID: `R1-M8-10-SOURCE-PAGE-EVIDENCE-I006`
- 상태: `CODE_GREEN / PRODUCTION_DEPLOYMENT_PENDING`
- 정본: 공식 Daon_User 작업공간, 배포 기준 SHA `e6c0e358d22cc01afcf2dfc2218d6203c918c8b2`

| 시각 | 단계 | 상태 | 확인·변경 | 검증 | 다음 작업 |
| --- | --- | --- | --- | --- | --- |
| 2026-08-21T12:55:58+09:00 | 실제 PDF 운영 재현 | RED | 승인된 `daon-knowledge-llm-guide.pdf` 1회 업로드. POST Source 202, Processing status GET 200 반복 후 UI 오류 재현. | 운영 PostgreSQL read-only: Run `completed`, Job `dead_letter`, safe code `PAGE_EVIDENCE_UNAVAILABLE`, Source `processing`; PDF 6페이지·의미 사실 34개 중 완전 문자열 일치 5개. | 인덱스 실패 조건 TDD |
| 2026-08-21T13:05:00+09:00 | 원인 확정 | RED | 실제 의미 사실의 재서술을 페이지 원문 완전 포함으로 요구해 29개를 근거 없음으로 오판. 빈 Context/UI 오류가 원인이 아님을 확정. | 실제 요청·NPM access·DB Run/Job/Source/Understanding 계보 결속. | 기존 정확 일치 경로 보존 + 페이지 원문 fallback |
| 2026-08-21T13:08:00+09:00 | TDD 최소 교정 | GREEN | 모든 의미 사실이 위치하면 기존 semantic fact Chunk 유지. 하나라도 위치 불명확하면 검증된 Parser page text를 페이지별 Evidence Chunk로 사용. SourceVersion·page·EvidenceSpan 고정 유지. | RED 1 fail/4 pass → GREEN 5/5. 관련 46/46, API 전체 491 pass·42 skip·137 subtests, Ruff PASS. | Commit·Push 후 API·document-worker 배포 승인, 현재 실패 Source의 제한 복구와 실제 질문·Citation 검증 |

## 보호 경계

- 공개 API·DTO·Migration·Provider 설정·권한·same-origin 경로 변경 0건.
- 실제 Provider 호출은 승인된 PDF 처리 1회에서만 발생했다. 질문·Studio Provider 호출은 아직 0건이다.
- 기존 Mobile 삭제와 다른 dirty/untracked 파일은 수정·Stage하지 않는다.
- 현재 실패 Source 복구는 배포 후 exact Source/Run/Job 한 건에 한정하고 별도 실행 전 승인 경계를 다시 확인한다.

# R1-M8-10-SOURCE-PAGE-EVIDENCE-I006 진행 기록

- 작업 ID: `R1-M8-10-SOURCE-PAGE-EVIDENCE-I006`
- 상태: `COMPLETED / PRODUCTION_ACTUAL_PASS`
- 정본: 공식 Daon_User 작업공간, 배포 기준 SHA `6b5d6fa4572a44e755ae7385031f95bab51b15b9`

| 시각 | 단계 | 상태 | 확인·변경 | 검증 | 다음 작업 |
| --- | --- | --- | --- | --- | --- |
| 2026-08-21T12:55:58+09:00 | 실제 PDF 운영 재현 | RED | 승인된 `daon-knowledge-llm-guide.pdf` 1회 업로드. POST Source 202, Processing status GET 200 반복 후 UI 오류 재현. | 운영 PostgreSQL read-only: Run `completed`, Job `dead_letter`, safe code `PAGE_EVIDENCE_UNAVAILABLE`, Source `processing`; PDF 6페이지·의미 사실 34개 중 완전 문자열 일치 5개. | 인덱스 실패 조건 TDD |
| 2026-08-21T13:05:00+09:00 | 원인 확정 | RED | 실제 의미 사실의 재서술을 페이지 원문 완전 포함으로 요구해 29개를 근거 없음으로 오판. 빈 Context/UI 오류가 원인이 아님을 확정. | 실제 요청·NPM access·DB Run/Job/Source/Understanding 계보 결속. | 기존 정확 일치 경로 보존 + 페이지 원문 fallback |
| 2026-08-21T13:08:00+09:00 | TDD 최소 교정 | GREEN | 모든 의미 사실이 위치하면 기존 semantic fact Chunk 유지. 하나라도 위치 불명확하면 검증된 Parser page text를 페이지별 Evidence Chunk로 사용. SourceVersion·page·EvidenceSpan 고정 유지. | RED 1 fail/4 pass → GREEN 5/5. 관련 46/46, API 전체 491 pass·42 skip·137 subtests, Ruff PASS. | Commit·Push 후 API·document-worker 배포 승인, 현재 실패 Source의 제한 복구와 실제 질문·Citation 검증 |
| 2026-08-21T13:24:00+09:00 | 운영 배포·기존 실패 건 제한 복구 | GREEN | Commit/Push `6b5d6fa4`; ysna API·document-worker만 rebuild/recreate. 저장된 Understanding 결과를 새 인덱서로 재투영해 Source `ready`, Job `completed` attempt 2, IndexVersion 1, EvidenceSpan 6으로 복구. 기존 attempt 1 실패 이력은 보존. | API healthy, document-worker up. 복구 중 추가 Provider 호출 0. Application Notebook service에서 기존 Source binding exact replay 확인. | 로그인된 실제 Chrome 검증 |
| 2026-08-21T13:28:12+09:00 | 운영 실제 화면·HTTP | PASS | 사용자 Chrome의 exact Notebook URL에서 `daon-knowledge-llm-guide.pdf` 1건, Version `sv-a947c6f2c1efb9c61e731191d5d6a6b9`, `사용 가능`, 질문 Context `Raw Source` 확인. Source 오류·재시도 버튼 0, console warn/error 0. | NPM access 결속: Notebook 200, Context 200(length 464), same-origin `/bff/api/workspaces/.../sources?notebook_id=...` 200(length 374). | 근거 질문·Citation·Studio는 별도 Provider 실행 승인 후 진행 |

## 보호 경계

- 공개 API·DTO·Migration·Provider 설정·권한·same-origin 경로 변경 0건.
- 실제 Provider 호출은 승인된 PDF 처리 1회에서만 발생했다. 질문·Studio Provider 호출은 아직 0건이다.
- 기존 Mobile 삭제와 다른 dirty/untracked 파일은 수정·Stage하지 않는다.
- 실패 Source 복구는 승인된 exact Source/Run/Job 한 건에 한정 완료했다. 다른 Source·운영 데이터·Provider 설정은 변경하지 않았다.

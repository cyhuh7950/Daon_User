# Foundation B5 제약·준수 점검표 Actual Gate

- 시각: 2026-08-15T05:25:21+09:00
- 범위: Phase B 메뉴 5 `Studio → 제약·준수 점검표`
- 판정: VERIFIED_COMPLETE

## TDD

- RED: 기존 UI는 `ComplianceChecklist`의 items를 한 문단으로 평탄화하여 항목·판정·근거·조치를 구분하지 못했다.
- GREEN: `제약·준수 점검 결과` semantic table을 추가하고 `compliant|non_compliant|needs_review`를 안전한 한국어 판정으로 표시한다.
- 다른 산출물 유형의 기존 generic renderer는 유지한다.

## Actual PostgreSQL 15.18

- disposable DB에서 migration `0001→0016` PASS.
- 실제 `build_structured_output()`로 `compliance_checklist` Canon을 생성했다.
- GenerationSettingsSnapshot, GenerationRequest, StudioOutput, OutputVersion, EvidenceReference를 실제 저장하고 `draft→review_requested→in_review→approved`를 전이했다.
- Repository 재조회에서 `needs_review` 판정과 `ruleset-v3` lineage를 복원했다.
- XLSX export bytes는 `PK` signature와 SHA-256 checksum 일치를 확인했다.
- Gate 결과: `1 passed`, `B5_CURRENT_0016_PASS`, `B5_COMPLIANCE_CONTENT_LINEAGE_VERSION_XLSX_PASS`, `B5_CLEANUP_REMAINING_0`.

## Actual Browser 1920×1080

- same-origin Web/API: `127.0.0.1:14185` → BFF → `127.0.0.1:18485`.
- Daon 승인 지식 1건과 Raw Source 1건을 함께 선택했다.
- 실제 질문 후 `제약·준수 점검표` 설정을 열고 목적·독자·분량·구성·XLSX·승인 필수를 입력했다.
- 생성 결과는 2개 점검 행을 항목·판정·근거·조치 열로 표시했다.
- Version 2 편집 후 reload 및 Library 재진입에서 Version 2/1, Citation 2건, structured table을 복원했다.
- 검토 요청→승인 요청→Step-up 승인→XLSX 내보내기를 실제 클릭했다.
- Step-up password는 승인 후 소거됐고 DOM에 내부 API URL, SQLSTATE, Traceback, password가 노출되지 않았다.
- full-page screenshot bytes: `103779`.
- Browser finalize 후 API/Web listener `18485/14185`는 0이다.

## 자동 회귀

- Menu 5 focused Python: `27 passed, 1 skipped`.
- Actual PG: PASS 및 cleanup 0.
- Fresh 전체 회귀와 evidence manifest는 메뉴 종료 직후 별도 실행한다.

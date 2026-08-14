# Foundation B6 비교·데이터 표 Actual Gate

- 시각: 2026-08-15T05:40:00+09:00
- 범위: Phase B 메뉴 6 `Studio → 비교·데이터 표`
- 판정: VERIFIED_COMPLETE

## TDD

- RED: 기존 UI는 ComparisonTable rows를 한 문단으로 평탄화해 기준·현재·상태·양쪽 근거를 구분하지 못했다.
- GREEN: `비교·데이터 결과` semantic table을 추가하고 항목·기준·현재·상태·근거를 표시한다.
- `same|changed|missing|conflict`는 안전한 한국어 상태로 투영하며 다른 산출물 renderer는 유지한다.

## Actual PostgreSQL 15.18

- disposable DB migration `0001→0016` PASS.
- 실제 `build_structured_output()` 비교 content를 GenerationSettingsSnapshot, GenerationRequest, StudioOutput, OutputVersion, EvidenceReference로 저장했다.
- Repository 재조회에서 `changed` 행과 양쪽 Evidence를 복원했다.
- approved OutputVersion XLSX bytes `PK` signature와 SHA-256 checksum 일치를 확인했다.
- Gate: `1 passed`, `B6_CURRENT_0016_PASS`, `B6_COMPARISON_CONTENT_LINEAGE_VERSION_XLSX_PASS`, DB remaining 0.

## Actual Browser 1920×1080

- Daon 승인 지식 1건과 Raw Source 1건을 선택하고 실제 질문 후 비교·데이터 표를 생성했다.
- 화면은 `시장 규모` 변경 행과 `규제 상태` 동일 행을 항목·기준·현재·상태·근거 표로 표시했다.
- Version 2 편집 후 reload 및 Library 재진입에서 Version 2/1, Citation 2건, 표를 복원했다.
- 검토 요청→승인 요청→Step-up 승인→XLSX 내보내기 actual same-origin PASS.
- API log는 Question 200, generation 201, versions GET/POST 200/201, review/approval/step-up 201, XLSX GET 200을 기록했다.
- password 소거, 내부 API URL/SQLSTATE/Traceback/secret 노출 0.
- screenshot bytes `42883`, Browser finalize 및 ports `14186/18486` listener 0.

## 환경 복구

- 최초 standalone Web은 build 후 정적 chunk 복사 전이라 SSR 화면만 표시하고 hydration하지 않았다.
- 최신 `.next/static`을 동일 build의 standalone 경로에 복사해 재기동했고 root/chunk가 일치한 뒤 actual Browser Gate를 수행했다.
- 제품 코드 우회나 가짜 성공 상태는 추가하지 않았다.

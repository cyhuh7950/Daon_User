# Foundation B9 Library 통합 관리 Actual Gate

- 시각: 2026-08-15T06:20:00+09:00
- 범위: Phase B 메뉴 9 `Library → 저장 산출물 통합 관리`
- 판정: VERIFIED_COMPLETE

## TDD와 UI

- RED: Library 목록은 제목·Source·Version·상태만 표시해 서로 다른 산출물 유형을 구분하지 못했다.
- GREEN: 다섯 산출물에 유형 Label과 유형별 Icon class를 표시하고, 선택 상세 Header에도 유형·상태를 함께 표시한다.
- 선택 상세는 기존 Version 이력·Citation·편집·AI 재생성·설정 변경·검토·승인·내보내기·전달·생산 지식 등록 경계를 그대로 사용한다.

## Actual PostgreSQL

- PostgreSQL 15.18 disposable DB migration `0001→0016` PASS.
- 운영과 동일한 Organization·Workspace Egress 정책 Projection 아래 5개 산출물 유형을 한 Library 조회로 복원했다.
- 각 유형의 Canon content·Evidence·Version·approved lifecycle·PDF/XLSX/JSON/DOCX bytes/checksum을 기존 실제 Repository Gate와 함께 검증했다.
- `B9_CURRENT_0016_PASS`, `B9_LIBRARY_FIVE_TYPES_VERSION_LIFECYCLE_EXPORT_PASS`, cleanup 0.

## Actual Browser 1920×1080

- same-origin Source·Knowledge·Studio 조회 200, Library 5개 유형·Source 2·Version 2·승인 상태 표시 PASS.
- 근거 기반 보고서 상세 재진입에서 Version 이력과 Raw·Daon Citation을 복원했다.
- 편집 새 Version, AI 재생성 새 Version, 검토 요청, 승인 요청, Step-up 승인, Export GET 200을 실제 클릭했다.
- Step-up password 소거, internal URL/SQLSTATE/Traceback/secret 노출 0, console warning/error 0, screenshot 31824 bytes.
- Browser finalize 및 ports `14189/18489` listener 0.

# Foundation B7 지식 구조도 Actual Gate

- 시각: 2026-08-15T06:00:00+09:00
- 범위: Phase B 메뉴 7 `Studio → 지식 구조도`
- 판정: VERIFIED_COMPLETE

## TDD와 UI

- RED: 기존 UI는 nodes/edges의 ID·label·confidence·evidence를 한 문단으로 평탄화했다.
- GREEN: `지식 구조 결과`에 근거 노드 카드, 검증 상태, Evidence, 노드 간 관계를 표시한다.
- 노드 `verified|unverified|needs_review`만 안전한 한국어 상태로 투영한다.

## Actual PostgreSQL

- PostgreSQL 15.18 disposable DB migration `0001→0016` PASS.
- 실제 `build_structured_output()` nodes/edges를 Canon OutputVersion과 EvidenceReference로 저장하고 approved 전이했다.
- Repository 재조회에서 verified node와 page Evidence를 복원했다.
- JSON export bytes/checksum 일치, `B7_CURRENT_0016_PASS`, `B7_KNOWLEDGE_MAP_CONTENT_LINEAGE_VERSION_JSON_PASS`, cleanup 0.

## Actual Browser 1920×1080

- Daon 승인 지식+Raw Source 질문→지식 구조도 설정·생성 actual PASS.
- `Daon 승인 지식`, `원본 PDF` 두 노드와 `Daon 승인 지식 → 원본 PDF`, `근거 순서` 관계를 표시했다.
- Version 2 편집→reload Library 재진입→Version 2/1·Citation 2 복원→검토·Step-up 승인→JSON GET 200.
- password 소거, internal URL/SQLSTATE/Traceback/secret 노출 0, screenshot 42883 bytes.
- Browser finalize 및 ports `14187/18487` listener 0.

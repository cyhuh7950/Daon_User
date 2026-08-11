# R1 사용자 제품과 Evidence Hub 분리 Stage A 완료 보고

## 판정

`COMPLETED` — 재작업 2, 독립 Runtime 재검토와 최종 SLA·Lifecycle 교정까지 검증했다.

## 기준과 승인 경계

- 공식 작업공간: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`
- Branch/기준 HEAD: `master` / `98707a05ef2a37154fe18302cc429a19415b9588`
- 설계 SHA-256: `F3A2990C4388C60F707E29417C45134BDD401F4AC201B6E73498E8B620810718`
- 갱신 계획 SHA-256: `D5EF6AC33FEB3F71B2C1F44629F5C37DD32481BC53492028781EA2FFC76F6003`
- 갱신 작업지시 SHA-256: `BD55BCDED348FCCCF9503B43DD464D1CF2F439A71960EEC67D6B9C8D7D9E9DCE`
- 적용 조항: Global Constraints, Task 0~4 중 Product import graph·Manifest partial fail-close, 실제 PDF Processing·Question 연결, Question/Citation exact Safe DTO, 150초 Processing Deadline·10초 Status 제한·operation Lifecycle과 작업지시 §3.3·§5·§6.

승인 Web 핵심 4경로는 HEAD 원본이며 종료 diff 0이다. 기존 사용자 삭제 27건, Cargo 변경, Native Evidence·보고서와 다른 미추적 파일은 되돌리거나 Stage하지 않았다. Commit·Push·배포·Browser·Installer는 수행하지 않았다.

## 독립 재검토 결과 — 실제 Runtime Processing 계약

- 정본 판단: `DocumentProcessingStatus.processing_state`는 DB `processing_runs.state`를 그대로 반환하고 terminal은 `completed`다. 기존 계획·작업지시와 재작업 2의 `processing_state=ready` 문구는 실제 Runtime과 충돌하므로, 어울1이 범위 변경이 아닌 기존 PDF→처리→질문 흐름의 계약 교정으로 `source_state=ready`, `processing_state=completed`, `job_state=completed`를 승인했다.
- RED: actual React에서 terminal `completed`와 queued→leased→processing→completed sequence를 주입했을 때 status가 1회만 조회되어 기대 4회에 미달했고, timeout도 1회 조회 후 loading에 머물렀다(Processing focused 0/2).
- GREEN: status exact 7-key·Safe ID·lineage를 매 조회 검증하고, 횟수 제한 대신 Upload 시작부터 monotonic 전체 Deadline 150초와 Poll 1000ms를 적용했다. 각 Status 요청은 10초 request-local Controller와 operation Controller를 결합한 Signal로 중단되며 전체 Deadline 전이면 재시도한다.
- terminal 3중 상태와 lineage가 모두 맞을 때만 `selectedSource`와 질문을 활성화한다. queued·leased·processing은 loading에서 fresh status를 재조회한다.
- 전체 Deadline은 `PROCESSING_TIMEOUT`, lineage 불일치는 `PROCESSING_LINEAGE_MISMATCH`, malformed DTO는 `PROCESSING_STATUS_INVALID` Safe error로 종료하며 질문 invoke는 0이다.
- Upload는 operation Signal을 직접 받고, Status는 operation+request-local linked Signal, Poll wait는 operation Signal을 받는다. 새 Upload·Unmount는 이전 operation을 중단하고 이전 결과의 State 반영·후속 Status 호출을 0으로 유지한다.
- 실제 React로 12초를 넘긴 14번째 Status의 정상 completed 성공, 영구대기 Status의 request-local 중단·전체 Deadline Safe timeout, 새 Upload·Unmount 중단, queued→leased→processing→completed 후 질문·page 2 Citation 도달과 lineage·malformed 회귀를 확인했다. actual same-origin Upload·Status fetch의 Signal 전달도 고정했다.
- Minor: missing client CSS, app-path route, NFT reference를 각각 독립 Fixture와 exact `MANIFEST_ASSET_MISSING` path assertion으로 추가했다. 이는 verifier 기능 결함이 아니라 기존 통합 테스트의 독립 coverage 누락이어서 새 세 assertion은 첫 실행부터 기존 fail-close 구현으로 3/3 PASS했다.

## 재작업 2 결과

### F1 — Product Entry import graph와 Build Manifest

- RED: 신규 `@daon-user/ui/new-product` explicit subpath가 재귀 import한 `nested-product.js/deferred_actual`을 놓쳤고, Next client manifest와 Vite index가 참조한 누락 JS/CSS도 `ok=true`였다(0/2).
- GREEN: Product Entry의 상대 import/export와 모든 `@daon-user/*` workspace package `exports`를 재귀 추적해 전이 Source를 자동 검사한다. 신규 explicit subpath는 수동 목록 추가 없이 scan된다.
- Next root/page build manifest, app-path manifest, NFT, client-reference manifest와 Vite `index.html`이 참조하는 route·chunk·CSS의 실제 존재를 검사한다. missing/symlink/invalid artifact는 fail-close한다.
- exact BFF 예외는 기존 승인 source와 route runtime+NFT+source-map 계보가 모두 일치하는 server chunk에만 한정한다. 광범위 directory/shared chunk 예외는 없다.

### F2 — Processing 3중 완료(독립 재검토로 정본 교정됨)

- RED: Product Shell이 status body를 확인하지 않고 upload 후 무조건 Source를 질문 가능 `ready`로 승격했다(0/1).
- 당시 `processing_state=ready` 합성 Fixture를 기준으로 단일 조회를 GREEN 처리했으나 실제 Runtime terminal과 달라 독립 재검토에서 위 `completed`+bounded polling 계약으로 교체했다. 이 항목의 이전 GREEN 판단은 현재 완료 근거로 사용하지 않는다.

### F3 — Question/Citation exact Safe DTO

- RED: API는 citations 객체, invalid Citation ID/page, unknown outer/data field를 성공 반환했다(1/6). Shell은 citations 객체를 state에 넣은 뒤 render `.map`에서 crash했다(0/1). numeric ID도 문자열 강제변환으로 통과했다(0/1).
- GREEN: `apps/web/lib/question-answering-api.js`는 outer exact `data|meta`, data exact 5 keys, citations 배열(최대 10), Citation exact 5 keys, 모든 ID와 page를 검증한다. 성공 meta의 trace/workspace ID도 검증한다.
- Product Shell은 adapter 응답을 state에 넣기 전에 같은 Safe DTO를 재검증하고 Citation URL을 exact same-origin workspace/citation/page URL로 projection한다. URL 생성 throw·외부 URL·lineage 불일치는 모두 `QUESTION_RESPONSE_INVALID` Safe error로 전환한다.
- 정상/객체 citations/invalid string·numeric ID/page/unknown field/invalid citation URL actual React에서 render crash 0을 확인했다.

## 이전 Stage A·재작업 1 보존 결과

- Evidence Hub는 `apps/evidence-hub` 독립 로컬 앱이며 Evidence-only CSS와 자산은 제품 Source/Bundle에서 제거됐다.
- Web 인증 진입, Windows Login-only/Workspace 기본 Route, same-origin PDF→processing→question→Citation 실제 Adapter 연결을 보존한다.
- `/operations`는 어울1 승인 Stage A Safe unavailable surface이며 Network/Adapter 0이다. 기존 Recovery 자산은 Stage C 실제 관리자 기능 복원 대상으로 보존한다.
- Desktop Operations authz 재조회 중 같은 Session의 검증 Projection과 선택 Route를 유지하고 최종 실패는 fail-close한다.

## Fresh 검증

| 검증 | 결과 |
| --- | --- |
| Evidence/Platform | PASS · 26/26 |
| Product boundary | PASS · 12/12 |
| Workspace/Desktop/Recovery actual React | PASS · 47/47 |
| Quality gate | PASS · 28/28 |
| Question/Operations | PASS · 41/41 |
| Workspace 전체 | PASS · 37/37 |
| Evidence build | PASS · 20 modules · CSS 9.15 kB · JS 217.94 kB |
| Web build + post Gate | PASS · compile/type/6 pages · 220 files · 0 violation · 0 boundary error |
| Desktop build | PASS · 25 modules · CSS 34.84 kB · JS 213.98 kB |
| 전체 Product Gate | PASS · 231 files · 0 violation · 0 boundary error |
| Desktop lint / syntax | PASS · 4 files / scanner·Source upload API |
| `git diff --check` | PASS · whitespace error 0 |
| 승인 Web 4경로 / staged | PASS · diff 0 / staged 0 |

Browser 실행 제품 Source 내부 URL·loopback scan은 0건이다. Build·자동 테스트 결과를 Production Chrome 또는 Windows NSIS actual PASS로 승격하지 않는다.

## 미해결·후속 Gate

- Stage B: 신규 Source 목록·Studio actual 수직 연결과 실제 제품 Gate.
- Stage C: 보존 Recovery 자산으로 실제 관리자 `/operations`를 권한·연결 Gate 아래 복원.
- 후속 실제 검증: Production Chrome same-origin Network와 Windows NSIS 사용자 Journey.

## 표준 결과 계약

status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단
--- | --- | --- | --- | --- | --- | ---
COMPLETED | R1-USER-PRODUCT-SEPARATION-01-I001 | 실제 Runtime `completed` terminal, 150초 Deadline·10초 Status 제한·operation Lifecycle·Safe 실패와 Manifest 세부 회귀를 TDD/행동 검증 | exact status/lineage Gate, monotonic polling, Upload/Status/Wait Signal, 새 Upload·Unmount abort, completed 뒤 질문·Citation, CSS/app-path/NFT 독립 assertion, 보고서 정합화 | 26/26·12/12·47/47·28/28·41/41·37/37, 3종 build, Web 220/0/0·전체 Gate 231/0/0, lint·syntax·diff·복원·stage PASS | Stage B 신규 Source 목록·Studio, Stage C Operations 복원, Chrome/NSIS actual Gate | 어울1의 독립 재검토 종료 판정과 후속 Stage 판단

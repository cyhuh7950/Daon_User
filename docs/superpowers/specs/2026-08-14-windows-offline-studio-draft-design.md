# Windows Offline Studio 초안 설계

## 1. 상태와 목표

`R1-M8-10`은 Windows Local-private Workspace에서 네트워크가 차단된 상태로 업무 문서 초안을 생성·편집하고, 암호화 저장소에 실행 계보와 동기화 대기 상태를 보존한 뒤, 재연결 시 사용자가 승인한 항목만 Cloud-sync로 전달하는 작업이다.

선행 계약은 다음과 같이 완료되어 있다.

- `R1-M7-02`: Local-private Source와 Managed Local Model만 사용하는 Offline 질문·근거 계약
- `R1-M8-01`: `configuring → confirmed → submitted` 생성 설정과 불변 `GenerationSettingsSnapshot`
- `R1-M8-06`: Template·Section·Evidence·검토 상태를 갖는 업무 문서 초안
- `R1-M5-03`: SQLCipher Metadata·AEAD File Store·Canonical Envelope·OS Secure Store Key
- `R1-M5-05`: Preview·Step-up 승인 Snapshot·암호화 Offline Queue·재개 Batch·충돌 명시 선택

목표는 이 계약을 새 정본이나 우회 계층으로 복제하지 않고 Windows Local Service와 Tauri 화면 흐름으로 결합하는 것이다.

## 2. 선택한 방식

기존 경계를 조립하는 `Local-first + approved sync` 방식을 사용한다.

1. Desktop UI는 Tauri command만 호출한다.
2. Tauri는 command-bound 단기 Token으로 Loopback Local API를 호출한다.
3. Local Service는 Managed Local Model과 Local-private Source만으로 초안을 만든다.
4. 초안·설정·RunSnapshot·OutputVersion·PendingOperationReference를 기존 SQLCipher Canon Envelope에 append-only로 저장한다.
5. 오프라인에서는 편집과 Sync Draft Queue 보존까지만 허용한다.
6. 재연결 후 Tauri Native Cloud Client가 기존 Sync 공개 API를 사용해 Preview와 사용자 승인을 수행한다.
7. 현재 Session·Membership·정책·Version·Step-up이 유효한 승인 항목만 전송한다.

Desktop 전용 평문 Queue, 별도 SQLite, Browser fetch, Cloud 우선 생성, 오프라인 승인 성공 위장은 사용하지 않는다.

## 3. 사용자 화면과 운영 흐름

### 3.1 화면 구조

Windows Workspace의 Studio 영역은 1920×1080 기준 세 영역으로 구성한다.

- **초안 설정**: 목적, 독자, Local Source, Template, 분량, 검토 조건, Local Model 상태
- **초안 편집**: Section 제목·본문·Evidence·`unverified` 경고, 저장 상태, Version
- **동기화 대기함**: 로컬 Version, 대상 Cloud Workspace, 승인 상태, 충돌 상태, 마지막 검증 시각

기본 본문·폼은 12px, 보조 설명은 10px, 제목은 16px을 사용한다. 긴 설명 박스를 상시 노출하지 않고 `i` 아이콘 Tooltip·Popover로 처리한다. 오류는 사용자가 할 수 있는 조치와 Safe Error Code만 표시한다.

### 3.2 정상 흐름

```text
설정 입력
→ 설정 확인
→ 불변 GenerationSettingsSnapshot 저장
→ Local Model 초안 생성
→ RunSnapshot·StudioOutput·OutputVersion 저장
→ Section 편집 시 새 OutputVersion append
→ 동기화 대상 선택
→ PendingOperationReference와 Queue draft 저장
→ 재연결 감지
→ Sync Preview 확인
→ Step-up 포함 명시 승인
→ 현재 권한·정책·Version 재검증
→ 승인 항목만 Batch 전송
→ reindex_requested 또는 conflict 표시
```

초안 생성과 편집은 네트워크 상태와 무관하게 Local Service에서 완료된다. 재연결은 자동 전송의 트리거가 아니라 사용자에게 Preview 확인이 가능해졌음을 알리는 상태 변화다.

### 3.3 실패·대체 흐름

- Local Model 없음·중지: `LOCAL_MODEL_UNAVAILABLE`, 생성 0건
- Local Key 잠김·철회: `LOCAL_KEY_UNAVAILABLE`, 읽기·쓰기·재개 0건
- 설정 미확정: `SETTINGS_NOT_CONFIRMED`, RunSnapshot·OutputVersion 0건
- Evidence 없는 Section: 생성은 가능하지만 `unverified` 경고 고정
- Offline 상태: Queue는 `draft | awaiting_approval`, Cloud 전송·최종 승인 0건
- 승인 만료·철회·권한 축소: `SYNC_APPROVAL_REQUIRED` 또는 현재 권한 Safe Error, 전송 0건
- Version 충돌: `SYNC_VERSION_CONFLICT`, 자동 병합·덮어쓰기 0건
- 장치 Revoke·Sync Key 폐기: Queue 접근·재개 0건

## 4. 구성요소와 책임

### 4.1 Local Offline Studio Domain

Local Service에 하나의 조정 서비스 경계를 둔다.

```text
OfflineStudioService
  confirm_settings(...)
  generate_draft(...)
  append_edit(...)
  get_draft(...)
  queue_sync_preview(...)
```

이 서비스는 선행 Domain 계약을 재사용한다.

- `GenerationRequest`로 설정 확인·잠금
- `LocalConversation` 또는 동등한 Local Retrieval Port로 Local-private Evidence 선택
- `LocalDraftGeneratorPort`로 Managed Local Model 생성
- `DocumentDraft`로 Section·Evidence·검토 상태 정규화
- Local Canon Repository로 불변 계보 저장

`LocalDraftGeneratorPort`는 Local Model만 받는다. External·server_internal·Daon 후보와 자동 Fallback은 인터페이스에서 허용하지 않는다.

### 4.2 암호화 Local Canon

기존 `LocalEncryptedStore.canonical_envelopes`를 그대로 사용한다. 새 DB 파일이나 평문 JSON을 만들지 않는다. Canon entity allowlist에는 아래 두 종류만 추가한다.

- `GenerationRequest`
- `GenerationSettingsSnapshot`

기존 허용 Entity를 함께 사용한다.

- `Run`, `RunSnapshot`
- `StudioOutput`, `OutputVersion`
- `PendingOperationReference`

저장 순서는 다음과 같다.

1. confirmed `GenerationRequest`
2. immutable `GenerationSettingsSnapshot`
3. submitted `GenerationRequest` 새 Version
4. `Run`·`RunSnapshot`
5. 최초 `StudioOutput`·`OutputVersion`
6. 편집마다 `previous_version_id`가 직전 Version을 가리키는 새 `OutputVersion`
7. Sync 선택 시 `PendingOperationReference`

모든 payload는 `data_area=local_private`, Tenant에 종속되지 않는 Local Workspace UUID, schema version, canonical text, SHA-256 digest와 생성 시각을 가진다. 원문 Section body는 SQLCipher 경계 안에만 저장하며 Log·Audit·Evidence에는 digest와 opaque ID만 기록한다.

### 4.3 RunSnapshot 계약

Offline RunSnapshot은 최소 다음을 고정한다.

- Local Workspace ID
- GenerationRequest·SettingsSnapshot ID/Version
- Local SourceVersion·Citation/Evidence ID
- Local Model Artifact·Deployment Digest
- Template·review condition
- 네트워크 상태 `offline`
- egress `none`
- 생성 시각과 Trace ID

실행 중 Snapshot UPDATE를 금지한다. 재생성은 새 Run·RunSnapshot·OutputVersion을 만든다.

### 4.4 Offline Sync Queue

기존 `sync_queue_states`를 사용한다. Queue에는 Operation Reference, approval state, manifest digest, batch cursor, conflict reference만 저장하며 Content·Token·Key·Cloud URL은 저장하지 않는다.

오프라인 생성 직후에는 `draft`만 허용한다. Server Preview가 만들어지면 `awaiting_approval`, 사용자 승인 후 `approved`, 전송 중 `transferring`, 충돌 시 `conflict`, 완료 후 `reindex_requested`를 append한다. 기존 Version을 UPDATE·DELETE하지 않는다.

`list_resumable_sync_operations()` 결과가 있다고 자동 전송하지 않는다. Tauri 화면이 항목을 표시하고 사용자가 재개를 선택한 뒤에만 Native Cloud Client가 기존 Sync API를 호출한다.

### 4.5 Loopback Local API와 Tauri Bridge

새 **공개** API는 만들지 않는다. Local Service의 command-bound Loopback API에 Offline Studio 전용 내부 명령만 추가한다.

```text
POST /local/v1/studio/settings/confirm
POST /local/v1/studio/drafts/generate
GET  /local/v1/studio/drafts/{id}
POST /local/v1/studio/drafts/{id}/versions
POST /local/v1/studio/drafts/{id}/sync-queue
```

Capability는 read/write로 분리하고 command, method, exact path, 최대 body byte를 allowlist에 고정한다. Browser Origin·Proxy Header·query string·wildcard path·초과 body·재사용 nonce는 기존 middleware에서 거부한다.

React는 `fetch`, XHR, WebSocket을 사용하지 않고 Tauri invoke만 호출한다. Rust Bridge는 exact DTO allowlist, size cap, timeout, Content-Length, JSON response shape와 Safe Error를 검증한다. Local port, Token, storage root, key material과 내부 stack을 JS에 반환하지 않는다.

### 4.6 재연결 Cloud Sync

Cloud 동기화는 기존 R1-M5-05의 다섯 경로와 승인·재개·충돌 의미를 유지하되, Sync Item 계약을 SourceVersion과 OutputVersion을 구분하는 Versioned 계약으로 확장한다. 새 경로를 만들지 않는다.

- Sync Operation Preview 생성
- Operation 조회
- Step-up 승인
- Transfer Batch
- Conflict Resolution

Native Cloud Client는 승인된 public origin만 사용한다. Browser 코드는 Cloud URL을 알지 못한다. 전송 직전에 Server가 현재 Session·Device·Membership·Workspace ACL·Egress 정책·Step-up·Source/Output Version을 다시 검증한다.

Local-private 원본과 Local OutputVersion은 변경하지 않는다. Cloud에는 새 Version으로 Copy/Publish하고 Audit·Trace·Approval Snapshot과 연결한다. 실제 M6 재색인이 끝나기 전에는 `reindex_requested`만 표시한다.

#### 4.6.1 공개 Sync Item 계약

기존 Source Sync 요청을 깨지 않도록 `item_kind`의 기본값은 `source_version`으로 둔다. 공개 요청과 Domain은 다음 필드를 사용한다.

```text
item_kind: source_version | output_version = source_version
source_version_id: string | null
output_version_id: string | null
dependency_item_ids: string[] = []
```

검증 규칙은 다음과 같다.

- `source_version`: `source_version_id`만 필수이고 `output_version_id`와 `dependency_item_ids`는 비어 있어야 한다.
- `output_version`: `output_version_id`만 필수이고 `dependency_item_ids`에는 같은 Operation 안에서 먼저 전송해야 하는 `source_version` Item ID만 허용한다.
- 두 Version ID가 모두 있거나 모두 없으면 `SYNC_ITEM_INVALID`이다.
- 기존 Client가 `item_kind`를 생략하고 `source_version_id`를 보내는 요청은 이전과 동일한 Source Sync로 처리한다.
- Operation fingerprint, Preview digest, Approval Snapshot, Manifest digest, Conflict와 Audit에는 `item_kind`, 선택된 Version ID, 정렬된 dependency ID가 모두 포함된다.
- 승인 범위에 Output Item의 dependency가 빠져 있거나 dependency가 완료되기 전에 Output을 전송하면 `SYNC_DEPENDENCY_REQUIRED`로 fail-close하고 Output 전송·Canon write는 0건이다.

OutputVersion의 전송 payload는 일반 JSON이 아니라 `application/vnd.daon.offline-studio-output+json` Canon Bundle이다. Bundle은 Local OutputVersion, 직전 Version ID, GenerationSettingsSnapshot, RunSnapshot, Section·Evidence Reference, Local SourceVersion dependency ID와 각 Canon digest를 포함한다. Server는 실제 bytes의 SHA-256과 Manifest digest를 대조하고 exact key·schema version·Workspace·lineage·dependency 완료 상태를 검증한다. Browser나 Local Client가 Cloud Canon ID를 지정할 수 없다.

#### 4.6.2 Cloud Output import 경계

`ObjectQueueSyncTransferPort`는 `item_kind`로 전송을 분기한다.

- `source_version`: 기존 `area=source` Copy/Publish 동작을 그대로 유지한다.
- `output_version`: `area=output`으로 Object Queue에 제출하고, Server가 deterministic Cloud ID를 발급해 Cloud `GenerationSettingsSnapshot`, `GenerationRequest`, `StudioOutput`, `OutputVersion`을 만든다.

Cloud Canon은 Local ID를 Record ID로 재사용하지 않는다. 새 Cloud Canon payload에는 Local Workspace·Run·Settings·Output Version ID와 digest를 `offline_import_lineage`로 보존하고, 현재 Cloud Workspace 정책 Projection과 Import Actor·Trace·Approval Snapshot을 함께 고정한다. GenerationRequest는 `configuring → confirmed → submitted`, OutputVersion은 `generating → draft`의 기존 상태 전이를 통과한다. Local Evidence dependency는 해당 Source Item의 Sync Target과 digest로 보존하되 실제 Cloud SourceVersion·EvidenceSpan이 없는 상태에서 `EvidenceReference`를 거짓 생성하거나 `unverified`를 verified로 승격하지 않는다.

같은 Idempotency Key와 같은 Bundle digest는 동일 Cloud OutputVersion을 반환한다. ID나 digest가 다르면 `IDEMPOTENCY_KEY_REUSED`, Lineage·dependency·현재 권한·정책이 다르면 안전 오류로 거부한다. Object 제출 뒤 Canon transaction이 실패하면 성공 TargetVersion을 기록하지 않고 재시도는 동일 deterministic ID로 수렴한다.

`sync_target_versions.target_version_id`는 Output Item에서 실제 Cloud OutputVersion `record_id`를 가리킨다. Target record와 Audit는 `item_kind`를 보존한다. Source Item의 기존 Target 의미와 응답 필드는 바꾸지 않는다.

### 4.7 PostgreSQL Migration 0014와 배포 호환성

Migration `0014_sync_output_versions`는 `sync_preview_items`, `sync_manifest_items`, `sync_target_versions`에 `item_kind`를 추가한다. Preview·Manifest에는 nullable `output_version_id`와 immutable `dependency_item_ids text[] NOT NULL DEFAULT '{}'`를 추가한다. 기존 `source_version_id`는 nullable로 바꾸되 아래 exact-one CHECK를 둔다.

```text
(item_kind='source_version' AND source_version_id IS NOT NULL AND output_version_id IS NULL)
OR
(item_kind='output_version' AND source_version_id IS NULL AND output_version_id IS NOT NULL)
```

기존 행은 `item_kind='source_version'`, `dependency_item_ids='{}'`로 deterministic backfill한다. Rolling deployment 중 구 API가 계속 INSERT할 수 있도록 DB default `source_version`을 유지한다. OutputVersion ID는 Local Canon ID이므로 Cloud `output_versions` FK로 거짓 결속하지 않는다. 대신 `sync_target_versions`에 nullable `target_output_version_id`를 추가하고, Output Item에서는 `target_output_version_id=target_version_id`를 강제한 뒤 Tenant·Workspace scoped Cloud `output_versions` FK로 결속한다. Source Item에서는 이 열이 null이며 기존 Target 의미를 유지한다. dependency 배열은 중복 없는 정렬된 Safe ID만 허용하는 Insert 검증 Trigger를 거쳐 Preview·Manifest와 Approval Snapshot digest에 동일하게 고정한다.

Upgrade는 backfill count, exact-one CHECK, digest·Approval Snapshot 결속, RLS와 기존 Source operation 재생을 실제 PostgreSQL에서 검증한다. Downgrade는 Output Item 또는 이를 참조하는 Target·Conflict·Batch가 하나라도 있으면 `SYNC_OUTPUT_DOWNGRADE_BLOCKED`로 fail-close한다. Output Item이 없을 때만 0013으로 되돌리고 기존 Source 행과 Operation 상태를 보존한다.

## 5. 데이터·보안 불변 조건

1. Local-private 원문과 초안은 승인 전 외부 전송 0건
2. External Provider 자동 Fallback 0건
3. 평문 SQLite·JSON Queue·임시 파일 0건
4. Browser의 API 절대주소·localhost·127.0.0.1·Docker 주소·`NEXT_PUBLIC_*` 직접 호출 0건
5. Local API는 loopback·instance-bound·short-lived command token만 허용
6. Key·Token·Password·원문·Local Path·Cloud 내부 URL Log/Evidence 노출 0건
7. Cross Workspace read/write/search/sync 0건
8. Version 충돌 자동 병합·덮어쓰기 0건
9. 승인 철회·만료·권한 축소 후 전송 0건
10. 동일 Idempotency Key 재요청의 중복 Version·전송 0건

## 6. 변경 범위

예상 제품 변경 범위는 다음으로 제한한다.

- Local Service Offline Studio Domain·DTO·command registry
- `LocalEncryptedStore` Canon allowlist와 Offline Studio 저장 Adapter
- Tauri Local Studio Bridge와 command 등록
- Desktop Offline Studio Adapter·상태 모델·Pane
- 기존 Sync API를 호출하는 Native 재연결 Adapter 연결
- 기존 다섯 Sync 경로의 Item DTO·Domain·PostgreSQL Adapter·OpenAPI 확장
- Migration `0014_sync_output_versions`와 actual PostgreSQL upgrade·rollback Gate
- Output Queue 제출과 Cloud Studio Canon import Adapter
- Unit·Local Integration·Rust Contract·Actual React·Windows installed Gate

Web BFF, Web Workspace Studio, Egress 정책값, 인증 모델과 공개 Sync 경로 수는 변경하지 않는다. 공개 Sync Item DTO와 PostgreSQL Schema는 위 Versioned 호환 계약 안에서만 확장한다. Object Storage는 기존 `source | output` 영역과 Object Queue만 재사용하며 새 Bucket·Key 규칙·외부 Provider를 만들지 않는다.

## 7. 검증 계약

### 7.1 TDD·자동 검증

- 설정 미확정·Local Model 부재·Local Key 잠김의 write 0 RED
- Offline 생성·편집 후 Canon 계보와 digest·previous version
- Restart 후 SettingsSnapshot·RunSnapshot·Draft·Queue 복구
- SQLCipher DB/WAL/SHM/File/Log 전체 고유 Canary 평문 0
- Network socket 차단 중 생성·편집 성공, 외부 연결 시도 0
- 다른 Workspace UUID의 Draft·Queue 조회 0
- Loopback capability/method/path/body/nonce 부정 Matrix
- React 실제 click으로 설정 확인→생성→편집→Queue 표시
- React 제품 코드 fetch/XHR/WebSocket·내부 주소 0
- Reconnect 전송은 승인 없음·만료·권한 축소·Version 충돌에서 transport 0
- 승인된 exact 항목의 Batch 재개와 중복 전송 0
- 기존 Source-only JSON 요청의 응답·재개·충돌 동작 무변경
- Output Item exact-one Version ID·dependency 승인/순서·Bundle digest 부정 Matrix
- Output 전송 성공 시 Cloud GenerationSettingsSnapshot·StudioOutput·OutputVersion·TargetVersion exact lineage
- Migration 0014 기존 Source backfill·RLS·CHECK·upgrade/reapply와 Output 존재 downgrade fail-close
- 기존 Local Storage·Sync·Generation Settings·Document Draft·Desktop 전체 회귀

### 7.2 실제 Windows Gate

실제 설치형 Windows App에서 다음을 수행한다.

1. Local-private Workspace와 Local Source 준비
2. 네트워크 차단
3. 초안 설정·확정·생성·편집
4. 앱·Local Service 재시작 후 동일 Version 복구
5. Process/Network에서 외부 Connection·DNS 0 확인
6. SQLCipher·암호문·Log 평문 Canary 0 확인
7. 연결 복구
8. Sync 대기함 Preview 확인
9. Step-up 승인 전 전송 0 확인
10. 승인 후 exact 항목만 Batch 전송
11. 충돌 Fixture에서 자동 덮어쓰기 0과 명시 선택 확인

화면은 종료 시 닫고 Process·Listener·Temporary credential·Fixture를 정리한다. 실제 Windows Gate가 없으면 코드 자동계약은 완료할 수 있어도 `R1-WIN-01 PASS`나 M8 Exit로 승격하지 않는다.

## 8. 완료 조건

- 오프라인 초안 생성·편집·Restart 복구
- GenerationSettingsSnapshot·RunSnapshot·StudioOutput·OutputVersion 계보 일치
- 암호화 Queue와 승인 전 외부 전송 0
- 재연결 승인 항목만 Sync, Source dependency 선행, Cloud OutputVersion 계보 일치, 충돌 자동 덮어쓰기 0
- 실제 설치 App·Network·암호화 증거
- 관련 자동 회귀·Desktop Build·Boundary·보안 Scan PASS
- 변경 파일·Evidence·Rollback·잔여 Process 0 보고

## 9. 제외 범위

- Web Offline 모드
- 모바일 Offline Studio
- 새 Cloud Sync 경로·새 Object Storage 영역
- 최종 승인·외부 Delivery·KnowledgeRegistration의 Offline 성공 처리
- Cloud 재색인 완료 위장
- 새로운 암호화 알고리즘·Key 저장소·별도 Local DB
- 자동 충돌 병합·자동 재개 전송

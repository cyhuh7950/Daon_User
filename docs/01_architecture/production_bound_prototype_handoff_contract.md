# Production-bound Prototype M3 승계 계약

## 1. 목적과 경계

이 문서는 `R1-M2-08`에서 검증한 M2 Prototype 자산 가운데 M3가 폐기하지 않고 승계할 항목과 실제 Adapter 연결 시 교체할 항목을 고정한다. M2 Evidence Hub는 제품 계약·상태·화면 흐름을 증명하지만 API·DB·LLM·File·Export·Delivery·Native Runtime의 실제 성공을 주장하지 않는다.

## 2. 재사용·교체 Matrix

| 영역 | M3 재사용 | M3 교체 | 승계 조건 |
| --- | --- | --- | --- |
| 정보 구조 | `navigation.json`, `screens.json`, `product_sitemap.md`, Route/Screen ID | 없음 | 승인 정본 밖 ID 생성 금지 |
| 화면 표준 | Design Token, 12px 본문·16px 제목, Tooltip/Popover, 네 폭 반응형 CSS | Evidence 전용 설명 문구 | 1920/1200/800/500과 접근성 회귀 유지 |
| Workspace | Workspace State, Pane/Drawer/Evidence 위치, 폭·Route 왕복 상태 보존 | Fixture Workspace와 Browser Session Check | 실제 저장은 Workspace Adapter 성공 응답 뒤에만 표시 |
| 자료·지식 | Source/Version/권위/가중치/충돌 순수 Model·Reducer와 안전 Code | `SourceKnowledgeAdapter` Fixture | M4~M7 실제 Source·Index·Query Adapter로 교체 |
| 모델·근거 | Routing Mode, Frozen Snapshot, Fallback, 비용, Citation Model·Reducer | `RunModelEvidenceAdapter` Fixture | M5 실제 Model Runtime·Evidence Adapter로 교체 |
| Studio | 설정→확정→제출→Version→승인·전달·등록 상태 계약 | `StudioWorkflowAdapter`, `StudioDeliveryAdapter` Fixture | M8·M9 실제 생성·승인·Export/Delivery Adapter로 교체 |
| 계정·보안 | Membership·Capability·Step-up·현재 권한 재검증 Model·Reducer | `AccountSecurityAdapter` Preview | M3·M9 실제 Auth·Policy Adapter로 교체 |
| 운영·복구 | Alert·Incident·waiting_model 재시도·G9·축소 운영 Model·Reducer | `OperationsStatusAdapter` Preview | M9 실제 Queue·Recovery·Notification Adapter로 교체 |
| Evidence Hub | 8개 여정·4플랫폼·검증 수준·M3 Owner Matrix | Browser-local 선택 상태와 전용 Check 상태 | M3 통합 검증 화면에서 실제 Adapter 상태로 대체 가능 |

## 3. 플랫폼 Owner와 검증 경계

| 플랫폼 | M3 Owner | M2가 증명한 항목 | M2 미실행·후속 검증 |
| --- | --- | --- | --- |
| Web | `R1-M3-01` | Next Production Build, Chrome 클릭, Route·상태·키보드·Console, 네 폭 | 실제 API/DB/LLM/File/Delivery는 M4~M9 |
| Windows | `R1-M3-02`, `R1-M3-03` | 공용 React UI와 `client_type=windows` 계약 Projection | Tauri EXE·설치·승인 IPC/Loopback·Local Service 실행은 M3 |
| Android | `R1-M3-04`, `R1-M3-05` | Navigation/Screen·Mobile Allowlist 계약 Projection | APK·실기기·Native Gateway는 M3 |
| iOS | `R1-M3-04`, `R1-M3-06` | Navigation/Screen·Mobile Allowlist·Build 준비상태 Projection | Archive/IPA·Simulator/실기기·서명은 M3 |

`client_type`, 화면 폭, NavigationPersona는 MembershipRole이나 Capability를 만들지 않는다. Android/iOS는 DOM 기반 `packages/ui`를 Import하지 않으며 M2 Hub의 Mobile 표시는 Native 실행 증거가 아니다.

## 4. 실제 Adapter Owner

| Adapter 경계 | 실제 연결 Owner | M2 상태 |
| --- | --- | --- |
| Workspace/Project·동기화 | M3·M4 | `deferred_actual` |
| Source·Vision/LLM·ASR·Parser/OCR·Index | M4·M5·M6 | `prototype_fixture` + `deferred_actual` |
| 검색·질문·Citation·Authority | M7 | `prototype_fixture` + `deferred_actual` |
| Studio 생성·Version·승인 | M8 | `prototype_fixture` + `deferred_actual` |
| Export·Delivery·생산 지식 등록 | M9 | `prototype_fixture` + `deferred_actual` |
| Auth·Policy·Audit·Operation·Recovery | M3·M9 | `prototype_fixture` + `deferred_actual` |

## 5. 통신·보안 경계

- Web Browser는 same-origin 상대 경로만 호출한다. 내부 API 주소는 BFF·Reverse Proxy·Server 계층에만 둔다.
- Windows는 승인된 Tauri IPC 또는 제한된 Loopback 경계만 사용한다. Browser 코드에 Local Service Host/Port를 노출하지 않는다.
- Android/iOS는 인증된 HTTPS Public Gateway만 사용한다. Docker 내부 Host/Port나 Web DOM UI를 재사용하지 않는다.
- 실제 Adapter는 현재 Tenant·Membership·Capability·Workspace·Source ACL을 매 요청 재검증한다.
- Secret, Credential, 개인정보, Raw Provider 오류, 내부 주소와 Chain-of-Thought를 DOM·Console·Evidence에 기록하지 않는다.

## 6. M2가 완료로 주장하지 않는 항목

- 실제 외부 API·DB·Migration·Queue·LLM·File·Export·Delivery·Backup·Restore·Update·Rollback 실행
- Windows Tauri Build·설치·IPC·Local Service 성공
- Android APK·실기기·Native Gateway 성공
- iOS Archive/IPA·Simulator·실기기·서명 성공
- Browser Session 선택·Check 상태의 서버 저장 성공
- `deferred_actual` 또는 `unavailable` 상태의 실제 기능 PASS

후속 실제 성공 판정은 `R1-M3-01`~`R1-M3-06`, M4~M9 Work Order와 테스트 계획의 해당 Gate 증거로만 승격한다.

# Audit Event Core 계약

## 목적과 경계

R1-M4-02는 Actor·Trace·Policy Version·안전한 변경 전후를 가진 불변 `AuditEvent`와 append-only hash-chain을 제공한다. 이 Core는 실제 HTTP·FastAPI·DB·Migration·Auth·RLS·Notification 성공을 주장하지 않는다.

## 불변 Event와 Chain

- Event는 Lock 안에서 단조 증가 `sequence`, 이전 Event Hash와 SHA-256 Hash를 원자적으로 확정한다.
- Hash 입력은 `event_hash` 자체만 제외한 모든 감사 의미 필드다. JSON Key 정렬, 고정 Separator, UTF-8, UTC 고정 시각을 사용한다.
- 첫 Event는 64자리 `0` Genesis Hash를 사용한다.
- Event·변경 Projection·Metadata는 깊은 불변 값으로 반환하며 내부 저장 List를 노출하지 않는다.
- Store 공개 API는 `append`, `read`, `list`, `verify_integrity`뿐이다. Update·Delete·Replace·Clear는 제공하지 않는다.

## 안전 Projection

Before·After·Metadata는 명시적으로 전달한 JSON Projection만 저장한다. 중첩 Key를 포함해 Password·Secret·Token·Credential·API Key·Raw Provider Error·Internal URL/Host 계열을 fail-close로 거부한다. Loopback·Private·Docker·Internal Host를 가리키는 값, 비직렬화 값, 비유한 숫자와 크기·깊이 한도 초과도 거부한다. 오류에는 입력값을 되비추지 않는다.

## 조회와 권한 경계

`list`는 Tenant를 필수 범위로 받고 Workspace·Action·Outcome·Trace·UTC 시간과 불투명 Cursor를 선택 Filter로 제공한다. 이는 순수 Read 계약이며 현재 Membership·Tenant 권한을 부여하거나 우회하지 않는다. 실제 Authorization·403/404 비노출은 R1-M4-04, HTTP/BFF/Gateway 적용은 R1-M4-05가 소유한다.

## 무결성과 보존

`verify_integrity`는 중복 ID, 순서·누락, Previous Hash와 Event Hash·의미 필드 변경을 안전 Code로 구분한다. 외부 Snapshot 검증은 Read-only이며 Restart·Durability를 주장하지 않는다. Audit 보존 1년은 R1-D009 정책 Metadata이고, 실제 저장·Legal Hold·Backup·Restore·Retention은 M5가 소유한다. Core에는 삭제 기능이 없다.

## 운영 검증

사용자·운영자가 Python을 직접 실행하지 않도록 저장소 루트 `npm run verify:api-audit`가 Python 3.14.3 Unit·Integration·Tamper Test와 결정적 증거를 실행한다. `--write`를 명시한 경우에만 R1-M4-02 증거를 갱신한다.

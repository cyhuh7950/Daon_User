# R1-M5-06 로컬·ysna-server 격리 검증 요약

- 검증 작업공간: 공식 OneDrive 정본, Branch `codex/r1-m5-06`.
- TDD RED: Retention Module·Migration `0005`·OpenAPI 6 Route·Runtime Route·Local Tombstone 부재를 각각 실패로 확인했다.
- Target GREEN: Retention Domain/Contract/Runtime HTTP 12건 통과, Retention Domain Coverage 92%, Runtime Coverage 88%.
- API 전체 회귀: 139건 통과, 23건 조건부 Skip. Skip은 PostgreSQL·MinIO·POSIX 전용 환경 조건이다.
- Local Service 전체 회귀: 92건 통과, 1건 조건부 Skip.
- Local 암호화 검증: SQLCipher DB에서 Tombstone Restart, Pending Ack, Device Ack, Device Revoke, Key Destruction과 평문 Canary 0건을 확인했다.
- OpenAPI/Runtime: 승인된 Method·Path 6종만 등록됐으며 실제 ASGI 흐름으로 멱등 요청, 현재 권한, Step-up 결합 거부, Hold 적용·해제, 유예 오류, Cancel을 확인했다.
- Quality Gate 최종 실행: Lint 8, Type 5, Unit 9, Contract 3, Build 8, Security 3, Independence 1 전부 통과, Failure 0.
- C01 공개 오류 계약: 승인된 신규 3코드만 OpenAPI enum에 추가했고 unavailable은 `RESOURCE_UNAVAILABLE`, 내부 검증·멱등·Version 충돌은 `INVALID_REQUEST`, Fixture Guard는 `CURRENT_ACCESS_DENIED`로 매핑했다. 현재 Retention Domain 오류 19종을 전수 매핑해 공개 enum 이탈 0건을 확인했다. RED 2건 확인 후 GREEN 6건, OpenAPI 9건, Quality Gate Contract/Security Test 37건과 Workspace Lint 16파일이 통과했다.
- 변경 범위: Browser 코드, 설정값, 의존성, 외부 서버, 기존 사용자·운영 데이터 변경 0건.

## ysna-server 격리 검증

- 승인 Root `/home/ubuntu/deploy/daon-user/R1-M5-06-C01`에 구현 SHA `0f3b1c1a1a19615c5986449ffe89cee005f0371b`를 Detached Checkout했다.
- 전용 Compose Project `daon_r1_m5_06_c01_0f3b1c1`의 PostgreSQL·MinIO·Network·Volume만 생성했다. 기존 Shared·Proxy·M5-05·운영 자원과 데이터는 조회·변경·삭제하지 않았고 검증 후 Cleanup도 하지 않았다.
- PostgreSQL `18.4`의 빈 Public Table 0을 확인하고 Migration 전 스키마 Backup을 생성했다. `0001→0002→0003→0004→0005`, 0005 무해 재적용, `0005→0004→0005`가 통과했으며 최종 Revision은 0005다.
- Backup SHA-256은 Migration 전 `d7225fd0d4e7b03985a8d92a22b5e98fbc5199de6abb2fc2d45ab9320adcc283`, Revision 0005 `4a3b53044eeb37bfbd6f9d9c30025b507bf02503cbed35e879dbddee4dd570d4`다.
- 실제 `daon_app`·`NOBYPASSRLS` 세션에서 Cross-Tenant·Cross-Workspace 조회 0, Cross-scope FK 거부, Active Hold 전이 거부, `retention_lineage` 삭제 거부를 확인했다.
- 전용 Bucket `daon-r1-m5-06-c01-0f3b1c1`의 Fixture만 사용해 30일 유예, Hold 적용·해제, Derivative 부분 실패, 실패 항목만 재시도, Local Ack Gate와 최종 `purged`를 확인했다. 비 Fixture 삭제는 0건이다.
- Runtime `/health/live`와 `/health/ready`는 실제 PostgreSQL·MinIO 연결에서 각각 200이었다. Retention Domain·Contract·6 Route Runtime·MinIO Target 13건, PostgreSQL Retention Integration 1건, Local Tombstone Target 2건이 통과했다.
- Server API 전체 회귀는 139건 중 138건이 통과했고 Node/Next 파일 존재성만 확인하는 1건은 Python Runtime Image에 Node가 없어 환경 오류였다. 해당 계약은 일회성 Ephemeral Harness에서 별도 통과했다.
- 동일 전용 DB에서 전체 회귀를 다시 실행했을 때 일부 고정 Fixture가 첫 실행 데이터와 충돌했다. 이는 재사용된 Test DB 문제라 유효 회귀 판정에서 제외했으며 제품 코드나 DB를 수정하지 않았다.
- Server Local 전체 회귀는 `TEMP=/tmp`를 지정한 읽기 전용 Checkout에서 91건 통과, Platform 조건 2건 Skip이다.

## 잔여 판단

- 서버 격리 검증 증거 수집은 끝났지만 최종 완료 판정은 어울1 소유이므로 Manifest는 `REVIEW_PENDING`으로 인계한다.

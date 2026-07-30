# R1-M5-06 로컬 검증 요약

- 검증 작업공간: 공식 OneDrive 정본, Branch `codex/r1-m5-06`.
- TDD RED: Retention Module·Migration `0005`·OpenAPI 6 Route·Runtime Route·Local Tombstone 부재를 각각 실패로 확인했다.
- Target GREEN: Retention Domain/Contract/Runtime HTTP 9건 통과, Retention Domain Coverage 92%.
- API 전체 회귀: 136건 통과, 23건 조건부 Skip. Skip은 PostgreSQL·MinIO·POSIX 전용 환경 조건이다.
- Local Service 전체 회귀: 92건 통과, 1건 조건부 Skip.
- Local 암호화 검증: SQLCipher DB에서 Tombstone Restart, Pending Ack, Device Ack, Device Revoke, Key Destruction과 평문 Canary 0건을 확인했다.
- OpenAPI/Runtime: 승인된 Method·Path 6종만 등록됐으며 실제 ASGI 흐름으로 멱등 요청, 현재 권한, Step-up 결합 거부, Hold 적용·해제, 유예 오류, Cancel을 확인했다.
- Quality Gate 최종 실행: Lint 8, Type 5, Unit 9, Contract 3, Build 8, Security 3, Independence 1 전부 통과, Failure 0.
- 변경 범위: Browser 코드, 설정값, 의존성, 외부 서버, 기존 사용자·운영 데이터 변경 0건.

## 검증 한계

- 현재 환경에는 `DAON_TEST_POSTGRES_DSN`과 MinIO Endpoint가 없고 Windows Docker CLI가 없으며 WSL 조회도 권한 거부됐다.
- 따라서 PostgreSQL 18.4의 `0001→0005`, Rollback/Reapply, 실제 `daon_app` RLS와 전용 Object Fixture 삭제 증거는 실행하지 못했다. 조건부 통합 테스트는 Skip 상태다.
- 외부 ysna-server 배포와 격리 자원 생성은 승인 범위 밖이므로 어떤 서버 명령도 실행하지 않았다.

# R1-M5-02 격리 Object·Queue 검증

이 Compose는 ysna-server의 `/home/ubuntu/deploy/daon-user` 아래 exact SHA 검증에만 사용한다. PostgreSQL 18.4·pgvector 0.8.2, MinIO `RELEASE.2025-09-07T16-13-09Z` ARM64 Manifest Digest를 고정한다.

- 전용 Compose Project·Network·DB Volume·Object Volume·Bucket만 사용한다.
- DB·Object Credential은 저장소나 환경값에 직접 기록하지 않고 Compose Secret 파일 참조로만 주입한다.
- Bucket 생성과 Prefix 정책은 검증 Service가 수행하고, Browser·Native Client는 내부 Endpoint·Bucket·Key를 호출하거나 받지 않는다.
- `shared-db`, `common`, `netdata`, `proxy`와 다른 Bucket·Volume을 참조하거나 변경하지 않는다.
- 종료 시 exact Checkout, Secret 파일, Test Object·Bucket, Container·Network·Volume을 제거하고 잔여 0을 확인한다.

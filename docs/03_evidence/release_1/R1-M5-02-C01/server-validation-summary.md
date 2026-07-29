# R1-M5-02-C01 서버 검증 요약

- 대상: ysna-server `aarch64`, exact SHA `f3da3c78a3fce3abecf94dff932df3cdb66d53d3`
- 격리: `/home/ubuntu/deploy/daon-user/R1-M5-02-C01` 아래 전용 Checkout·Runtime·Compose Project만 사용
- PostgreSQL: `18.4`, pgvector `0.8.2`; Migration `0002` 적용·head 재적용 PASS
- Object Suite: `16/16 PASS`; Replay ID/Flag, Claim, Crash, Retry/Dead-letter, 권한 재처리·이력, RLS, MinIO Digest/Metadata 포함
- Cloud Suite: `11/11 PASS`; Runtime Suite: `15/15 PASS`
- 복구: Migration downgrade base, 전용 DB 재생성, Backup Restore, upgrade head 후 Object `16/16 PASS`
- 실제 Process: Initial API `live=200/ready=200`; Object·DB 장애 각각 `live=200/ready=503`, 복구 후 `ready=200`; Worker/API SIGTERM Exit 0
- 검증 Fixture 복구: Production HTTPS Proxy Header, Local Identity SQLite 임시 경로, Probe 관찰 timeout을 제품 계약에 맞게 보완. 제품 코드·Compose·Adapter timeout은 변경하지 않음
- 정리: C01 Container·Network·Volume·Bucket/Test Object·Checkout·Secret·Backup 잔여 0. `shared-db`, `netdata`, `nginx-proxy-manager(proxy)` 상태 불변
- Secret·Credential·DSN·내부 Endpoint 원문은 기록하지 않음

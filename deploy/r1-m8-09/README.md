# R1-M8-09 Git 배포·TP-4 실행 안내

`ysna-server`의 `/home/ubuntu/deploy/daon-user` 격리 배포 기준이다. 기존 `common`, `proxy`, `netdata`, `shared-db`는 사용하거나 변경하지 않는다.

```bash
cd ~/deploy/daon-user
git fetch origin codex/r1-m5-07
git checkout --detach 86447c1
git rev-parse HEAD
docker compose -p daon-user -f deploy/r1-m8-09/compose.yaml config
docker compose -p daon-user -f deploy/r1-m8-09/compose.yaml up -d --build
docker compose -p daon-user -f deploy/r1-m8-09/compose.yaml ps
```

Migration·Backup·Rollback은 전용 DB/Volume에서 수행한다. TP-4는 실제 Browser 클릭으로 생성 설정·5종 파일·Version·Review·Approval·재승인·Delivery·KnowledgeRegistration을 검증하고 same-origin Network와 Mock 0건을 기록한다.

# R1-M5-07 Windows Native Login ysna 배포 보정 실행 프롬프트

`C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User\docs\02_work_orders\release_1\R1-M5-07-WINDOWS-NATIVE-01-D01_deployment_work_order.md`를 먼저 EOF까지 읽고 그대로 수행한다.

공식 Desktop 정본의 `master`와 `ysna-server:/home/ubuntu/deploy/daon-user`만 사용한다. 기존 `.env`, `backups/`, `secrets/`, 공용 PostgreSQL·Proxy·Network·Volume을 보존하고 Daon API 서비스만 Fast-forward·Build·재생성한다. Secret·Credential·DSN 값을 출력하지 않는다. 각 단계와 Rollback 준비를 지정 Progress에 기록하고 표준 결과 계약으로 보고한다. Commit·Push와 Native 실로그인은 수행하지 않는다.

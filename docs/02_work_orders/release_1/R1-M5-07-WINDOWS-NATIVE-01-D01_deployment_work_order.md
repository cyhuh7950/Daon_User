# R1-M5-07 Windows Native Login ysna 배포 보정 작업지시서

## 1. 승인과 기준선

- Work Order ID: `R1-M5-07-WINDOWS-NATIVE-01-D01`; Issue ID: `R1-M5-07-WINDOWS-NATIVE-01-I001`.
- 상태: `READY` · 2026-08-11.
- 신산님은 2026-08-11 Native 로그인 실패 원인이 ysna-server 구버전 배포임을 보고받고 최신 `origin/master` 재배포를 `그래`로 승인했다.
- 공식 로컬 정본은 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, Branch `master`; 배포 대상 서버 경로는 `ysna-server:/home/ubuntu/deploy/daon-user`다.
- 기존 제품 기준선은 Commit `66742bfd64c9799686875fd7ecae237b2cb3bd0c`; Task 6 문서 포함 승인 기준선은 착수 시 `origin/master`의 exact SHA를 기록한다.
- 어울2가 이 범위의 유일 Writer·배포 실행자다. Commit·Push는 수행하지 않는다.

## 2. 근본원인과 단일 목표

- 실제 Native 로그인 `POST /api/v1/auth/native/login` 3건은 공개 Proxy까지 도달했지만 ysna API가 모두 404를 반환했다.
- ysna checkout SHA `6ae48531f1911af256839b4307180a834172bf8f`에는 Native Login Route가 없고, 최신 `origin/master`에는 승인 Route가 있다.
- 목표는 ysna checkout을 최신 `origin/master`로 Fast-forward하고 Daon 전용 Compose의 `api` 서비스만 재빌드·재생성하여 Route를 제공하는 것이다.
- 제품 코드·DB Schema·Migration·Web·Document Worker·Object Storage·공용 Proxy·PostgreSQL·Network·Volume·`.env`·Secret 파일은 변경하지 않는다.

## 3. 허용 기록

- Create/append: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-01-D01_progress.md`
- Create: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-01-D01_completion_report.md`
- Append/update: `docs/03_evidence/release_1/R1-M5-07-WINDOWS-NATIVE-01/manifest.json`, `verification-summary.md`, 안전한 배포 결과 JSON/Markdown
- 서버에서는 Git Fast-forward, Daon API Image build/tag, `api` 컨테이너 재생성만 허용한다.

## 4. 사전점검

1. 로컬 `HEAD == origin/master`, Branch `master`, 사용자 삭제 31건·기존 미추적 문서 3건과 Task 6 Evidence 상태를 기록한다.
2. 서버 `master`, 현재 HEAD, origin URL, tracked diff 0을 확인한다. 기존 untracked `backups/`, `secrets/`는 보존하며 내용·Secret을 출력하지 않는다. 그 외 tracked 변경이 있으면 배포하지 말고 `BLOCKED_SERVER_DIRTY`로 보고한다.
3. `git fetch origin master` 후 현재 HEAD가 `origin/master`의 ancestor인지 확인한다. Fast-forward 불가면 Merge·Reset하지 않고 중단한다.
4. 현재 SHA와 대상 SHA 사이 `services/api/migrations`, `deploy/daon-user/compose.yaml` 변경이 0건인지 재확인한다. 하나라도 있으면 본 작업 범위를 넘으므로 배포하지 않는다.
5. `docker compose --env-file .env -f deploy/daon-user/compose.yaml config --quiet`로 Secret 값을 출력하지 않고 구성 유효성을 확인한다.
6. 현재 API Container ID·Image ID·Health와 Web/API 공개 Health를 기록한다. 현재 API Image를 `daon_user-api:rollback-R1-M5-07-WINDOWS-NATIVE-01-D01`로 보존한다.

## 5. 배포 실행

```bash
cd /home/ubuntu/deploy/daon-user
git pull --ff-only origin master
docker compose --env-file .env -f deploy/daon-user/compose.yaml build api
docker compose --env-file .env -f deploy/daon-user/compose.yaml up -d --no-deps api
```

- 각 명령은 단일 실행하며 중복 Build·재시작하지 않는다.
- API Health가 `healthy`가 될 때까지 조건 기반으로 추적하되 120초를 넘기면 새 실행을 시작하지 않고 현재 Container 상태와 Log의 Safe 오류만 수집한다.
- Log에서 Authorization·Cookie·Password·Token·DSN·API Key·Secret 값은 출력하지 않는다.

## 6. 검증과 Rollback

- 서버 checkout `HEAD == origin/master`, API Container가 새 Image를 사용하고 `healthy`, Web·Document Worker·Object Storage가 기존 상태를 유지함을 확인한다.
- 공개 `GET /api/v1/session`이 비인증 401을 유지하는지 확인한다.
- 실제 Credential 없이 `POST /api/v1/auth/native/login`에 빈 JSON을 1회 보내 404가 아닌 승인된 입력 오류 상태·Safe Error가 반환되는지 확인한다. Password·실계정·Cookie는 전송하지 않는다.
- 배포 후 공개 Web Health와 `/` 접근 상태를 확인한다. Browser 로그인이나 Native 실로그인은 어울1이 사용자 직접 입력 후 별도로 재개한다.
- API Health 실패, Route 계속 404, 기존 Web/API 회귀가 발생하면 새 컨테이너를 더 수정하지 말고 보존한 Rollback Image로 API만 재생성한다. Git은 Reset하지 않으며 Rollback 결과와 대상 SHA를 기록한다.

## 7. 완료·결과 계약

- 완료 조건은 Fast-forward, API Image Build, API healthy, Session 401 유지, Native Login Route non-404, 기존 서비스 보존, Secret scan 0과 안전한 Rollback 준비 증거다.
- 진행 기록에 시각·단계·명령·Exit·이전/대상 SHA·이전/새 Image ID·Health·공개 상태·오류·복구·다음 작업을 즉시 남긴다.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
- 배포가 완료되면 Native 앱의 실제 로그인 재시도는 수행하지 말고, 신산님에게 Password 직접 재입력과 로그인 버튼 클릭을 요청할 수 있도록 앱 상태만 유지한다.

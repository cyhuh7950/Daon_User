# R1-M2-05 S9 서버 검증 요약

- 상태: `COMPLETED`
- 불변 구현 SHA: `ac80b670c1606a22cac27c39f311ed3bd8980a42`
- ysna-server: `/home/ubuntu/deploy/daon-user/R1-M2-05/ac80b670c1606a22cac27c39f311ed3bd8980a42`, detached·사전/사후 Clean
- Architecture: Host·Docker·Node Image·uv Image 모두 ARM64
- Toolchain: Node `24.18.0`, npm `11.12.1`, Corepack `0.35.0`, uv `0.11.2`
- Migration: `NOT_APPLICABLE_NO_SCHEMA`, DB 명령 `0`
- 검증: `npm ci` 258 packages, Foundation `8/8`, Studio 포함 전체 `77/77`, Lint `11 files`, Production Build `3/3`, 7범주 Gate `PASS`, Failures `0`, Exit `0`
- 자원 보호: Container·Network·Volume 사전/사후 Hash 일치, 임시 Container·Network·Volume·생성 디렉터리 잔여 `0`
- GitHub: PR `#11`, Run `29863801985`, Job `88746856112`, Artifact `8508374894`, Required Check·Branch Protection 일치

서버 Gate는 구현 SHA를 직접 기록한다. GitHub Artifact 내부 Gate SHA는 PR 검증용 임시 Merge SHA `92b48dbd...`이며, Run의 연결 Head SHA `ac80b670...`와 분리해 기록했다.

초기 원격 셸 인용, 잘못 확장한 축약 SHA, Node 최소 이미지의 uv 부재는 각각 원인을 확인한 뒤 같은 승인 경계에서 복구했다. 잘못된 불완전 Clone과 임시 Tooling만 정확한 경로로 제거했으며 기존 서버 자원은 변경하지 않았다.

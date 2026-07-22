# R1-M2-06 ysna-server 검증 요약

- 판정: `COMPLETED`
- 검증 SHA: `780ca50725233227076a40f5adb2b5f1e05b1070`
- 격리 경로: `/home/ubuntu/deploy/daon-user/R1-M2-06/780ca50725233227076a40f5adb2b5f1e05b1070`
- 환경: Ubuntu `aarch64`, Docker ARM64, Node `24.18.0`, npm `11.12.1`, Corepack `0.35.0`, uv `0.11.2`, Python `3.14.3`, Rust `1.97.1`

## 검증 결과

- GitHub Required Check: Run `29893036228`, Job `88837174463`, exact Head SHA, `success`
- 의존성: `npm ci` 260개, Next `16.3.0-canary.93`, PostCSS `8.5.10`, Sharp `0.35.3`, Audit 전 심각도 0
- 자동 테스트: Account Security `21/21`, 선택 회귀 `98/98`, Workspace Lint 11개 파일 통과
- Production Build와 `/settings/account`, `/settings/organization`, `/workspaces/workspace-release-one` Runtime HTTP `200` 통과
- 공통 품질 게이트: lint·type·unit·contract·build·security·independence 전부 `PASS`, 실패 0, Exit `0`
- Schema·Migration 신호 0건으로 `NOT_APPLICABLE_NO_SCHEMA`; DB 명령 0건

## 복구와 정리

최초 두 실행은 제품 결함이 아니라 컨테이너 기본 npm 버전 불일치와 uv 부재로 중단됐다. 승인된 정확 버전과 공식 ARM64 uv를 격리 도구로 사용해 동일 방식을 유지했다. 첫 통과 원문의 Git SHA가 컨테이너 Git 부재로 `UNAVAILABLE`이어서 Git을 포함한 일회성 ARM64 컨테이너에서 전체 게이트를 다시 실행했고, 원문에도 exact SHA가 기록된 것을 확인했다.

검증 후 체크아웃은 Clean이고 `.venv`, `apps/web/.next`, `node_modules`, 임시 도구 디렉터리는 모두 0건이다. 검증 컨테이너·전용 Network·Volume·4310 Listen Port도 0건이며, 기존 Container·Network·Volume 이름 목록의 사전·사후 SHA-256이 각각 정확히 일치한다.

## 릴리스 경계

R1-D022에 따라 Next Canary는 개발·GitHub Check·ysna 격리 검증에만 허용된다. 안전한 Stable Next로 동일 게이트를 통과하기 전에는 운영 릴리스를 허용하지 않는다.

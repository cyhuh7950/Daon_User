# R1-M2-01 ysna-server 검증 요약

- 상태: `COMPLETED`
- 연결 복구: 기본 Sandbox가 사용자 SSH 설정을 사용하지 못한 것이 원인이었고 외부 DNS 장애가 아니었다. 이후 승인된 escalated `ssh ysna-server`/`scp`만 사용했으며 IP·대체 서버 우회는 없다.
- 승인 Root·Checkout: `/home/ubuntu/deploy/daon-user/R1-M2-01/c471fad58f124e3ad28e33d98486f139306c0d91`, detached HEAD, 사전·사후 Clean.
- Architecture: Host `aarch64`, Docker `aarch64`, Node·uv Image `arm64`.
- Toolchain: Node `24.18.0`, npm `11.12.1`, corepack `0.35.0`, uv `0.11.2`; Toolchain 검사와 `npm ci` Exit `0`.
- Migration: Migration Directory `0`, Schema File `0`, Source Migration Signal `0`; `NOT_APPLICABLE_NO_SCHEMA`, DB 명령 `0`.
- 공통 Gate: exact Git SHA, `PASS`, Exit `0`, lint/type/unit/contract/build/security/independence 전부 PASS, Failures `0`.
- 기존 자원 불변: Container `575b3f...9585`, Network `34a32d...3ee2e`, Volume `232abe...39c19` 사전·사후 일치.
- 정리: 임시 Container·Network·Volume·File과 `node_modules` 잔여 `0`; 검증 Checkout만 승인 Root에 Clean 상태로 유지.

서버 Gate 원문은 `R1-M2-01-server-quality-gate-result.json`과 `R1-M2-01-server-quality-gate-summary.md`에 보존한다.

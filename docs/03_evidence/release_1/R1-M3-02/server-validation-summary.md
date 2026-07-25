# R1-M3-02 ysna-server 검증 요약

## 판정

`PASS` — 어울1이 `ysna-server`의 `/home/ubuntu/deploy/daon-user/R1-M3-02`에서 최종 exact SHA `0a4c76b1ba9c165bd0adfbcd62dccdabc8f716d5`를 확인했다. 이번 어울2 작업은 전달받은 확인 사실의 문서화이며 서버 명령을 다시 실행하지 않았다.

## 최종 검증 근거

- Host: `aarch64`, Docker `29.6.1` `linux/arm64`
- 임시 Toolchain: Node `24.18.0`, npm `11.12.1`, Rust/Cargo `1.97.1`, uv `0.11.2`, git `2.39.5`
- CA 번들 누락은 임시 Toolchain Dockerfile의 `ca-certificates` 설치로만 보정했으며 제품 저장소 변경은 `0`이다.
- 최종 Quality Gate: Overall `PASS`, Exit `0`, Policy SHA-256 `9D9249713AB1436BE2CF805C23D62926CAC6C9FE75962FBA10EDF4A67488D4C3`
- `lint`, `type`, `unit`, `contract`, `build`, `security`, `independence` 7범주가 모두 `PASS`이며 Failures는 `0`이다.
- 최종 SHA 이전 독립 검증에서 Web Build `7/7`, Desktop Vite `42 modules`, Production Audit info/low/moderate/high/critical/total 전부 `0`을 확인했다. FIX-06 최종 Gate가 Build와 Security를 다시 `PASS`했다.

## 수정 계보

1. `8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa`: predecessor Lock 증거 2건 실패를 확인해 FIX-05로 정합화했다.
2. `b76aa30fbc493937fba0685910f9353dffbf359d`: CA 번들 보정 뒤 ARM64 Rust Compile에서 `icons/icon.png` 부재를 확인해 FIX-06으로 보완했다.
3. `0a4c76b1ba9c165bd0adfbcd62dccdabc8f716d5`: 최종 exact-SHA Quality Gate가 통과했다.

위 과정은 모두 어울1 검증 과정의 재작업 근거이며 정식 `FAILURE_REPORT`가 아니다. 유효 실패 횟수는 `0회`다.

## 격리·정리 근거

- DB 변경 없음, Migration `N/A`
- `shared-db`, `common`, `netdata`, `proxy` 미사용·미변경
- Container Hash 전후 동일: `ded7efe2d467ceb904ac8d48babcb7b90797ce55bf126e1a3ef7d2aac526dd76`
- Network Hash 전후 동일: `ed49fdd14e2ed9ce757a4f7a710564283b001b0b86e6a3c07b468581856bd1d6`
- Volume Hash 전후 동일: `f4f5c78edb7520598e3e700ee8892f49bd98a7e2ee95c4404ae9672373474220`
- 임시 Container `0`, 점검한 생성 Directory 6종 잔존 `0`, Git Dirty `0`
- 최종 exact Checkout은 유지했다.
- 임시 Toolchain Image와 원격·로컬 임시 Dockerfile은 제거했다.

세부 정형 증거는 `server-validation-manifest.json`을 따른다.

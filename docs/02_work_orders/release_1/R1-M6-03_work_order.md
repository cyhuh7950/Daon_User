# R1-M6-03 Managed Local Model 작업지시서

## 승인 기준과 범위

- 버전 `1.0` · 2026-08-01. Work Order `R1-M6-03`, Issue `R1-M6-03-I001`.
- 공식 작업공간은 `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`, Branch `codex/r1-m5-07`다. 어울1 직접 구현·단일 Writer를 유지한다.
- 승인 정본: `AGENTS.md`, 상세 설계 §10.1·§11.1~§11.4·§16·§17·§20, 구현계획 §6·§8·§15·§21~§24, 테스트계획, Baseline Manifest, R1-M6-01·02 결과를 EOF까지 읽는다.
- 목표는 Managed Local Model의 하드웨어 진단·추천·Artifact 검증·설치·시험·Update·Rollback·삭제 상태 계약이다. 실제 모델 다운로드, 외부 URL 접속, 설치 파일 실행, 운영 배포는 이 작업에서 하지 않는다.
- `D:\Project\Daon_User`, `C:\tmp`, 보호 Untracked 2개는 건드리지 않는다.

## 필수 계약

- Hardware 진단은 CPU/GPU/메모리/디스크를 비밀값 없이 구조화하고 `compatible | constrained | incompatible`를 결정한다.
- Artifact 상태는 `not_installed → downloading → verifying → installing → ready`, 보조 `updating | rollback | failed | uninstalling`이다.
- Artifact는 승인된 source URI/allowlist, SHA-256 Digest, 서명 상태, License, 역할·양자화·용량을 고정한다. Digest/서명/License가 없거나 불일치하면 설치하지 않고 `failed`로 Fail-close한다.
- Installation은 원자적 staging→verify→install 경계와 이전 Ready Version 보존을 사용한다. Update 실패는 이전 Ready Version으로 Rollback하고, 삭제 중인 Artifact를 Deployment에 노출하지 않는다.
- Deployment 상태는 `starting | warming | ready | busy | draining | crashed | incompatible`이며 Artifact·Hardware compatibility·Health가 확인된 경우만 `ready`다.
- 사용자 화면/어댑터는 Python·모델 서버 명령을 요구하지 않는다. 설치 전송·실행은 후속 승인된 Runtime/Node 작업에서만 연결한다.

## TDD·완료 증거

1. RED: Hardware incompatibility, URI/Signature/Digest/License 실패, staging 원자성, Update 실패 Rollback, uninstall 중 사용 차단을 검증한다.
2. GREEN: 네트워크·실제 파일 없이 결정론적 Manifest와 상태 Machine을 최소 구현한다. 테스트 Fixture는 가짜 Secret·실제 다운로드를 사용하지 않는다.
3. 회귀: M6-01·M6-02·API 전체·`py_compile`·`git diff --check`와 금지된 브라우저 절대주소 패턴을 확인한다.
4. 진행 파일 `docs/04_test_reports/release_1/R1-M6-03_progress.md`에 모든 단계·오류·복구·테스트·Commit을 기록한다.
5. 실제 Model 설치·서버 배포·Local Node 연결은 미검증으로 명시하고 `COMPLETED`로 과장하지 않는다. 결과는 `판정 → 판단 이유 → 조치`와 표준 결과 계약으로 보고한다.

# R1-M5-03-C02 완료보고

## 판정

`COMPLETED` — C01 구현의 Windows 동작과 공개 계약을 보존하면서 Linux CI의 Mypy·Coverage 실패를 플랫폼 구현 분리와 POSIX 파일 경계 보강으로 교정했다.

## 판단 이유

- 실패 SHA `989090fcf5bf3a74ad900082f30f4aee021c26c1`를 Ubuntu 24.04 ARM64에서 승인 Pin 그대로 재현했다. Mypy는 `ctypes.WinDLL`·`ctypes.get_last_error` 2건, Unit은 `83 passed, 2 skipped`이나 Coverage 73.74%로 Exit 1이었다.
- 공용 `safe_file`은 Facade만 유지하고 Win32와 POSIX 구현을 별도 모듈로 분리했다. Threshold·omit·skip·xfail·포괄 ignore로 우회하지 않았다.
- POSIX 구현은 directory descriptor 상대 접근, `O_NOFOLLOW`, final inode·single-link 검증, 동일 directory `os.replace`, 교체 후 inode 재검증과 directory `fsync`를 수행한다.
- 기존 Windows Handle·Junction·Hardlink·TOCTOU 계약과 Facade precheck injection을 보존했다.
- 구현 정본 `11a121e8063f8d3fb7803da072dae2b19edf78a7`에서 Ubuntu, Windows, GitHub Quality와 iOS Phase A를 모두 재검증했다.

## 조치와 결과

### 변경 범위

- `services/local-service/src/daon_user_local_service/safe_file.py`: 플랫폼 공용 Facade
- `services/local-service/src/daon_user_local_service_safe_file_win32.py`: 기존 Windows Handle 경계 분리
- `services/local-service/src/daon_user_local_service_safe_file_posix.py`: descriptor-relative POSIX 경계
- `services/local-service/tests/test_local_storage_correction.py`: POSIX symlink swap-after-precheck 회귀 계약
- `scripts/run-local-service-tool.mjs`: 실행 플랫폼 구현을 Coverage source에 명시

### 검증

- Ubuntu 24.04 ARM64 exact Pin: type PASS, `84 passed, 2 skipped`, 전체 Coverage 88.80%
- Windows: Ruff PASS, strict Mypy 16 files PASS, 집중 `20 passed, 1 skipped`, 전체 `85 passed, 1 skipped`, Coverage 91.23%
- Desktop JS `25 passed`; Rust unit 16 + contract 4 PASS
- Independence 859 files, violations 0
- Windows Quality Gate 36/36 PASS, failures 0
- GitHub Quality Run `30491561166`, Job `90710405083`: success
- GitHub iOS Phase A Run `30491561232`, Job `90710405427`: 최종 결과는 `github-ci.json`에 기록

## 분류·잔여 상태

- C01 결과 분류: `INCOMPLETE 1회`
- 동일 `issue_id` 정식 `FAILURE_REPORT`: 0회
- 구현 Commit: `b44e362431dc9bebc61c01bc4097b4361ed05241`
- POSIX 원자 복구 보완 Commit: `11a121e8063f8d3fb7803da072dae2b19edf78a7`
- Evidence Manifest: `docs/03_evidence/release_1/R1-M5-03-C02/manifest.json`
- 기능 범위·공개 API·데이터 계약·보안 경계·설정값 변경: 없음
- 공용 Compose·DB·Network 사용: 없음
- 제품 Process·Listener와 Test Container 잔여: 0
- 서버 C02 전용 clean Checkout 1개는 명시적 파괴 작업 승인 전 삭제 금지에 따라 보존: `/home/ubuntu/deploy/daon-user/R1-M5-03/C02-989090fcf5bf3a74ad900082f30f4aee021c26c1`

## 조치

C02 산출물과 검증 근거를 Evidence-only Commit으로 기록하고 어울1 검토에 인계한다. 서버 전용 Checkout은 신산님의 삭제 승인 시 정확 경로만 제거한다.

# R1-M5-03-C02 Linux CI 플랫폼 정합 보완 작업지시서

## 판정

`R1-M5-03-C01`은 Windows 로컬·설치형 검증을 통과했으나 PR #30의 필수 Ubuntu `Release 1 Quality Gate`가 `local-service-type`과 `local-service-unit`에서 실패했다. 따라서 C01의 `COMPLETED`를 수용하지 않고 `INCOMPLETE` 1회로 분류하며 `VERIFYING → CORRECTION_REQUIRED`로 전환한다. 정식 `FAILURE_REPORT`는 0회다.

## 승인 기준과 작업공간

- Issue ID는 `R1-M5-03`, Work Order ID는 `R1-M5-03-C02`다.
- 공식 작업공간은 `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`, Branch는 `codex/r1-m5-03`, 인계 HEAD는 `989090fcf5bf3a74ad900082f30f4aee021c26c1`이다.
- `D:\Project\Daon_User`와 `C:\tmp`의 Clone·Worktree는 읽기 전용 보존 자료다. 수정·삭제·작업 전환을 금지한다.
- PR은 `https://github.com/cyhuh7950/Daon_User/pull/30`, 실패 Run은 `30487672418`, Job은 `90697321691`, GitHub 합성 Merge SHA는 `46fda23f1f471b7e7d831fc7ccde5257546ce72e`, PR Head SHA는 `989090f...`다.
- 어울2가 이 범위의 유일한 코드 Writer다. 어울1은 결과 검토 전까지 같은 범위를 수정하지 않는다.

## 확인된 증거

- Ubuntu CI: lint 8 PASS, type 5 중 `local-service-type` FAIL, unit 9 중 `local-service-unit` FAIL, contract/build/security/independence PASS. Quality 전체는 36 Check 중 2 Failure다.
- Windows 공식 정본에서 CI와 같은 `node scripts/run-local-service-tool.mjs type`은 PASS, `unit`은 `85 passed`, Coverage `88.37%`로 PASS다.
- 플랫폼 차이는 C01에서 추가한 `safe_file.py`의 Win32/비-Windows 조건부 구현과 플랫폼별 Coverage·Mypy 해석을 우선 조사하되, 증거 없이 원인을 확정하지 않는다.
- 기존 iOS Run `30487672440`은 C01 SHA의 독립 증거이므로 취소·재시작하지 않는다.

## 조치 목표

- Ubuntu 24.04 계열에서 승인 Pin(Node·npm·uv·Python 3.14.3)과 exact PR Head를 사용해 아래 두 명령의 원문 오류를 재현하고 Progress에 Secret 없는 최소 진단을 기록한다.
  - `node scripts/run-local-service-tool.mjs type`
  - `node scripts/run-local-service-tool.mjs unit`
- 재현은 우선 ysna-server `/home/ubuntu/deploy/daon-user`의 격리 Checkout/Compose 또는 동일 Ubuntu Container에서 수행한다. 기존 `shared-db`, `common`, `netdata`, `proxy`를 사용하거나 변경하지 않는다.
- 플랫폼별 구현을 분리해야 한다면 공용 계약 모듈과 Windows Handle 구현, POSIX 구현을 명시적으로 나눠 Linux Mypy가 Win32 전용 ABI를 해석하지 않고 Linux Coverage가 실행 불가능한 Win32 Line 때문에 낮아지지 않게 한다. Coverage 제외·Threshold 하향·광범위 Ignore로 통과시키지 않는다.
- Windows Handle TOCTOU·Junction·Hardlink 방어와 C01의 DAONENC2·Migration·Protocol 계약을 그대로 보존한다.
- Linux POSIX 경로도 Symlink·Hardlink·Root 탈출을 Fail-close하고 원자 쓰기·고아 복구를 유지한다.

## 허용·제외 범위

- 허용: `services/local-service/src/daon_user_local_service/safe_file*`, 직접 영향받는 Local Storage 코드·Test·Mypy/Coverage Test 배치, C02 진행·Evidence·완료보고.
- C01 Metadata/Header/API/Protocol·Credential 의미, 공개 API, 의존성 Version, Coverage 85% 기준과 Quality Gate 정책을 약화하거나 임의 변경하지 않는다.
- CI만 통과시키는 skip·xfail·platform blanket exclude를 금지한다. 실제 플랫폼에서 실행 불가능한 OS 전용 모듈의 자연스러운 미수집은 허용하되 공용/POSIX 실행 경로의 Coverage를 숨기지 않는다.
- Linux 재현을 위해 운영 자원이나 사용자 기존 데이터를 변경하지 않는다.

## TDD·필수 검증

- 수정 전 Ubuntu type/unit 실패 원문과 Exit Code를 확보한다.
- 수정 후 같은 Ubuntu 환경에서 type PASS, 전체 unit PASS, Coverage 85% 이상을 확인한다.
- Windows에서 C01 집중 Test, 전체 Python type/unit, Junction·TOCTOU·Hardlink, Rust·JS 계약을 재실행한다.
- Independence와 공식 Quality Gate를 로컬에서 실행하고, Push 후 PR #30 새 SHA의 GitHub `Release 1 Quality Gate`와 `iOS Phase A Simulator`를 재실행이 아니라 새 Push 자동 Run으로 끝까지 확인한다.
- GUI/Simulator를 직접 사용하면 종료 즉시 닫고 잔여 Process 0을 확인한다.

## 진행·증거·결과 계약

- `docs/04_test_reports/release_1/R1-M5-03-C02_progress.md`에 착수, Ubuntu RED, 원인, 수정, Windows/Linux GREEN, Push, CI Run ID·결과, 오류·복구와 종료 직전을 기록한다.
- 기존 `R1-M5-03_progress.md`에 C01 `INCOMPLETE 1회`, C02 상태·정본·다음 작업을 기존 이력을 삭제하지 않고 추가한다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-03-C02/manifest.json`과 플랫폼별 결과 JSON에 exact SHA·명령·Exit·Coverage·CI Run/Job·자원 정리를 기록한다.
- `R1-M5-03_completion_report.md`를 C02 최종 결과로 갱신한다.
- 결과보고는 표준 필드를 포함하고 `판정 → 판단 이유 → 조치` 순서로 반환한다.
- 완료 전 Local/Origin SHA 일치, Working Tree Clean, 서버/로컬 Test 자원과 Process·Listener 잔여 0, `INCOMPLETE 1회`, 정식 `FAILURE_REPORT 0회`를 보고한다.

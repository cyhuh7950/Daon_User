# R1-M4-06-C01 Linux Local Service 품질 Gate 정합 보완 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-06-C01`.
- Branch `codex/r1-m4-06`, 기준 HEAD `977b03d4cf111d10f13f427b3e04977cc2fdd787`, 시작 Clean.
- PR #26 Quality Run `30423348869`, Job `90484444232`와 WSL 독립 재현 결과를 적용한다.
- 어울2가 이 Worktree와 범위의 유일한 Writer다. PR·CI 재실행·Merge와 완료 판정은 어울1 소유다.

## 판정과 단일 목표

- 판정: `MAJOR_GAP / CORRECTION_REQUIRED`.
- `local-service-type`은 Linux에서 `services/local-service/src/daon_user_local_service/main.py:151`의 `ctypes.WinDLL`을 typeshed가 제공하지 않아 `attr-defined` 1건으로 실패했다.
- `local-service-unit`은 52개 테스트가 전부 통과했지만 Windows 전용 Process inspection 분기가 Linux에서 실행되지 않아 전체 Coverage가 `81.36%`로 `85%` 기준에 미달했다.
- 목표: Windows Local Service의 실제 Parent/Ancestor 보안 계약과 기존 Coverage 기준을 완화하지 않고, Local Service type·unit 검사가 Windows와 Linux CI에서 모두 동일하게 통과하게 한다.

## 허용·제외 범위

- 허용: `services/local-service/src/daon_user_local_service/main.py`, 직접 관련된 Local Service 테스트, 최소 Quality 실행기 테스트, R1-M4-06 evidence·진행·완료보고.
- 원인상 필요한 경우 Windows API loader와 Process snapshot adapter를 작은 내부 함수·Protocol로 분리할 수 있다. 공개 API나 Bootstrap wire contract는 변경하지 않는다.
- 제외: `mypy`·Coverage 기준 하향, Local Service 검사 삭제·skip, Linux만 무조건 성공시키는 분기, 무근거 `type: ignore`, 보안 검증 완화, Token·Capability·Command·Browser 차단 의미 변경, Dependency/Lockfile 변경, UI·Cloud API·iOS 변경, 전체 구조 재작성.

## 구현 계약

- Windows 전용 API 접근은 정적 타입 검사에서 플랫폼 차이를 명시적으로 모델링한다. `ctypes`의 동적 속성을 전역에서 직접 참조해 Linux typeshed에 의존하지 않는다.
- Windows Parent/Ancestor 검증은 기존과 동일하게 실제 Process snapshot을 사용하고 fail-close한다. Snapshot 생성·순회·Handle close·오류 처리를 보존한다.
- OS binding처럼 실제 Linux에서 실행할 수 없는 최소 접착부만 Coverage 대상에서 제외할 수 있다. 이 경우 제외 사유를 코드 가까이에 명시하고, 분리된 판단·순회·오류 처리 로직은 플랫폼 중립 Fake/Stub으로 Linux에서도 단위 검증한다.
- Windows 실제 Process·Packaged Sidecar 검증과 Linux type/unit 검증을 모두 유지한다. 한 플랫폼의 성공으로 다른 플랫폼 증거를 대체하지 않는다.

## TDD·필수 검증

- RED: WSL/Linux에서 기존 HEAD로 `node scripts/run-local-service-tool.mjs type`이 `ctypes.WinDLL` 오류, `unit`이 52 PASS 후 Coverage 81.36%로 실패함을 진행 기록에 남긴다.
- GREEN: Windows 전용 loader/adapter의 성공, Snapshot 시작 실패, 첫/후속 열거 실패, Handle close, Parent chain 일치·불일치·cycle·깊이 제한을 플랫폼 중립 테스트로 검증한다.
- WSL 또는 동등 Linux에서 `node scripts/run-local-service-tool.mjs type`와 `unit`을 직접 실행해 exit 0, mypy 오류 0, 테스트 전부 통과, Coverage 85% 이상을 증명한다.
- Windows에서 Local Service unit/type/lint/security, 실제 Packaged Sidecar 최소 2회, Rust cross-language runtime, Parent 종료 후 Process·Listener 0을 재검증한다.
- 관련 Node Quality runner 테스트와 전체 `npm run verify:quality-gate -- --no-write`를 실행한다. GitHub CI 재실행은 어울1이 Push 후 추적한다.

## 진행·보고

- `docs/04_test_reports/release_1/R1-M4-06-C01_progress.md`에 착수, CI·WSL RED 증거, 설계 선택, 구현, 오류·복구, Windows/Linux 검증, 종료 직전을 단계마다 기록한다.
- 기존 `R1-M4-06_progress.md`와 완료보고에는 C01 후속과 최종 증거를 연결한다. Evidence에 secret·Token 원문을 남기지 않는다.
- 완료 후 C01 완료보고를 `판정 → 판단 이유 → 조치` 순서로 작성하고 단일 보완 Commit을 Push한 뒤 Local/Remote SHA·Clean과 표준 상태를 보고한다.

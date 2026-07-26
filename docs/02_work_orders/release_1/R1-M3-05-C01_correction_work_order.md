# R1-M3-05-C01 수정 작업지시서 — 승인 Deep Link 완결과 공통 Gate 원인 분리

## 1. 작업 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M3-05-C01` |
| 원 issue_id | `R1-M3-05-I001` |
| Attempt | `2` |
| 시작 상태 | Attempt 1 `BLOCKED`, 정식 실패보고 0회 |
| 단일 Writer | 어울2 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M3-05_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-05_attempt-2.md` |

원 작업지시서와 승인 설계·계획·테스트 계획 전체가 계속 정본이다. 이 수정지시서는 신산님이 2026-07-27 승인한 공개 Deep Link 계약을 적용하고 Attempt 1의 두 차단 항목만 닫는다.

## 2. 필수 수정

1. `sinsan-daon://app/<native_route_key>`만 Android Manifest와 Host Parser에서 수락한다.
2. `<native_route_key>`는 R1-M3-04의 기존 8개 승인 Route Allowlist만 허용한다. Scheme 대소문자·다른 Host·빈 경로·추가 Segment·Encoding 우회·미등록 Route는 Fail-close한다.
3. 정상 8개 Route와 대표 비정상 입력을 정적 계약 Test와 `adb shell am start` 실제 Emulator Test로 검증한다.
4. Deep Link 수신 후 기존 Navigation·상태 복원 계약을 깨지 않고 승인 Route로 이동하는지 확인한다.
5. 종료 시 `com.sinsan.daon`을 Force-stop하고 사용자가 실행한 Emulator는 종료하지 않는다.

## 3. 공통 Gate 실패 처리

- Attempt 1의 Desktop Rust `production_manager_error_fixtures_are_bounded_and_leave_no_processes` 실패를 Deep Link 변경 후 깨끗한 생성물 상태에서 재실행한다.
- 환경·잔존 Process·Port·Fixture Marker와 선행 R1-M3-03 Merge 기준에서 동일 Test가 통과했는지를 증거로 분리한다.
- Android 변경이 원인이 아니며 깨끗한 동일 환경에서 재실행이 통과하면 환경성 중단 복구로 기록하고 공통 Gate 전체를 다시 실행한다.
- 동일 실패가 재현되면 Desktop/Local Service Source를 이번 작업에서 수정하지 말고 `BLOCKED`로 보고한다. 원인 증거와 별도 선행 결함 Work Order 필요성을 제출한다.

## 4. 검증·완료 조건

- TDD RED→GREEN과 Android 전용 Gate PASS
- Debug APK 재빌드·Hash·서명·Package 확인
- Emulator 정상 8개 Deep Link와 비정상 Fail-close, Lifecycle·Route 복원 재검증
- Mobile 표준 회귀, 전체 Node, Gradle Lint·Debug/Release, Toolchain·Independence·Audit PASS
- 공통 7범주 Quality Gate PASS 또는 범위 밖 기존 Desktop 결함의 재현 증거와 `BLOCKED` 보고
- Crash·ANR·Secret·내부 URL 0건, 관련 없는 변경 0건
- Phase A가 모두 통과하면 개발 패킷 `COMPLETED`, 전체 Work Order 상태 `SIMULATOR_VERIFIED_PENDING_DEVICE`

Commit·Push·PR·Merge·SSH·서버 배포·사용자 Desktop GUI·Release Keystore 생성은 금지한다.

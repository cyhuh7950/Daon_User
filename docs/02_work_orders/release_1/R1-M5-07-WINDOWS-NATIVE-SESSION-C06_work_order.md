# R1-M5-07 Windows Native Session 2차 보안 보정 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WINDOWS-NATIVE-SESSION-C06` |
| issue_id | `R1-M5-07-WINDOWS-NATIVE-SESSION-C04-I001` |
| 재작업 차수 | `2차` |
| 상태 | `READY` |
| 기준선 | `master` HEAD `3f2c087` + 미커밋 C05 결과 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C06_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C06_completion_report.md` |

## 2. 판정

C05는 `NEEDS_CHANGES`이며 동일 issue의 `INCOMPLETE 2회`다. Redirect Critical은 해소됐으나 Secret 취소 수명주기, logout revoke fail-close, 제품 Refresh single-flight, 실제 reqwest Transport 시험, RFC3339 검증의 Important 5건이 남았다. C05의 유효 보정은 보존하고 남은 항목만 최소 수정한다.

## 3. 필수 구현 계약

1. Login·Refresh Request, Wire Credential, 응답 수집 Buffer는 `ZeroizeOnDrop` 또는 동등한 Drop Guard로 감싼다. `.send().await`·chunk 수집 Future 취소, 오류, `?` 조기 반환에서도 애플리케이션 소유 Secret이 Drop 시 지워져야 한다.
2. logout의 revoke 실패도 공통 pending-revoke fail-close 상태로 전환한다. 삭제 확인 전 `status()`는 `authenticated: true`를 반환하지 않는다. 다음 revoke 재시도 성공과 Target 부재까지 제품 경로에서 확인한다.
3. Refresh 동시 호출은 단순 직렬화가 아니라 같은 도착 세대의 in-flight 결과를 합친다. Gate 대기 전 generation ticket을 취득하고, 선행 Refresh가 generation을 갱신했으면 후속 대기 호출은 Vault의 새 Safe Projection을 반환하며 두 번째 HTTP를 보내지 않는다. 나중의 새로운 세대 호출 가능성은 보존한다.
4. Test Vault는 성공 write 뒤 replacement Credential을 실제처럼 반환해야 한다. 별도 `NativeRefreshFlow`에 제품 로직을 복제하지 말고 제품 `NativeSessionRuntime` 또는 동일한 단일 orchestration 함수를 테스트한다.
5. reqwest 실제 Adapter를 로컬 Test Server로 검증한다. 테스트 전용 Constructor만 고정 Production 경계를 우회할 수 있고 Production `fixed()`·Tauri Command에는 노출하지 않는다. 307/308 목적지 hit 0, Secret 전달 0, request timeout, chunked 128 KiB 초과 중단, malformed Content-Length와 중단 응답을 검증한다.
6. `expires_at`은 Lockfile의 `time 0.3.51`을 직접 Pin하고 RFC3339 parser로 UTC Timestamp를 검증한다. 잘못된 월·일·시·분·초, offset 또는 trailing 문자를 거부한다.
7. C05에서 이미 통과한 Redirect none, 5/20초 timeout, 128 KiB 상한, strict DTO, Win32 Blob zeroize, spawn_blocking, corrupt Target 삭제를 회귀 보호한다.

## 4. TDD 필수 사례

- Login/Refresh Future를 전송 대기 중 poll 후 drop했을 때 Request Drop Guard 실행
- chunk 오류·취소 및 Wire projection 조기 실패에서 Buffer/Credential Drop Guard 실행
- logout revoke 1차 실패 → status fail-close → 2차 revoke 성공 → Target 부재
- 실제 replacement를 반환하는 Vault에서 동시 Refresh N회 → HTTP 1회, 동일 Safe 결과
- 후속 새 generation Refresh는 별도 1회 허용
- 로컬 서버의 307/308 목적지 hit 0 및 fake Secret body 0
- timeout, chunked 초과, malformed Content-Length, truncated response fail-close
- 잘못된 RFC3339 달력·시간·offset·trailing 값 거부

RED → 최소 구현 → focused GREEN → 전체 회귀 순서를 진행기록에 남긴다.

## 5. 허용 경로와 검증

C05 허용 경로에 다음 C06 보고서만 추가한다.

- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C06_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C06_completion_report.md`

필수 검증:

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
node --test scripts/tests/desktop-tauri-shell.test.mjs
npm run verify:desktop-lint
git diff --check
```

허용 범위 밖 변경·전체 포맷·실제 Credential·Commit·Push·배포·Browser·설치는 금지한다. 사용자 삭제 31건, 미추적 문서 3건, Web/API/Local Service와 복원 파일을 보존한다.

## 6. 결과 계약

각 단계와 오류·복구를 C06 진행기록에 남기고 다음 형식으로 보고한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

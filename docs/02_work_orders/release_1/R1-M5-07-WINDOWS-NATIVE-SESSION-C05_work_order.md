# R1-M5-07 Windows Native Session 보안 보정 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-WINDOWS-NATIVE-SESSION-C05` |
| issue_id | `R1-M5-07-WINDOWS-NATIVE-SESSION-C04-I001` |
| 재작업 차수 | `1차` |
| 상태 | `READY` |
| 선행 기준 | `R1-M5-07-WINDOWS-NATIVE-SESSION-C04` 구현 커밋 `9eb9e8e` 및 내부 보안 검토 `NEEDS_CHANGES` |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C05_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C05_completion_report.md` |

## 2. 판정과 목표

판정은 `NEEDS_CHANGES`다. 기본 Redirect로 Password·Refresh Credential이 고정 Gateway 밖 HTTPS Host로 재전송될 수 있는 Critical 1건과 필수 보안 계약의 테스트 공백·Vault 삭제 실패·무제한 HTTP 응답·Secret 수명주기·Refresh 동시성의 Important 5건을 같은 개발자가 보정한다. 공개 API·사용자 기능·설계 범위는 변경하지 않는다.

## 3. 필수 보정 계약

1. `reqwest::redirect::Policy::none()`을 적용하고 모든 3xx를 `AUTHENTICATION_REQUIRED`로 fail-close한다. Redirect 응답을 받아도 두 번째 Host 요청과 Secret 전달은 0건이어야 한다.
2. Connect timeout 5초, 전체 Request timeout 20초를 명시한다. 성공 응답은 최대 128 KiB로 제한하고 `Content-Length` 선검사와 제한 스트리밍으로 초과 응답을 거부한다.
3. 기존 Lockfile의 `zeroize 1.9.0`을 직접 Pin하고 `Zeroize`·Drop Guard를 사용한다. Password·Refresh·Access와 직렬화 버퍼는 성공·오류·취소·조기 반환에서 가능한 범위까지 지운다. Win32 `CredReadW` Blob은 복사 후 해제 전 지우며 reqwest 내부 복사본처럼 애플리케이션이 통제할 수 없는 한계는 완료보고에 명시한다.
4. corrupt Credential을 읽거나 인증·Refresh가 실패했을 때 Vault 삭제 오류를 무시하지 않는다. 삭제 확인 전에는 인증 상태를 반환하지 않는 fail-close 상태를 유지하고 삭제 실패·재시도 경계를 검증한다.
5. `read → refresh HTTP → replacement write`를 비동기 단일 임계구역으로 직렬화한다. 한 Recovery 흐름에서 Refresh는 최대 1회이며 동시 호출이 같은 이전 Credential을 중복 사용하지 않아야 한다. 동기 Win32 Vault 호출은 비동기 Runtime의 UI/Event Thread를 장시간 막지 않도록 격리한다.
6. 응답은 JSON Content-Type, 최대 크기, `deny_unknown_fields`, 필수 식별자·`expires_at` 형식을 검증한다. Cookie, Redirect, malformed·oversize·unknown field는 Safe 오류로 닫는다.
7. 주입 가능한 Test Transport·Vault Port 또는 동등한 순수 경계를 사용해 실제 운영 Credential 없이 Login·Refresh·replay·실패·취소·동시성·삭제 실패를 결정론적으로 검증한다. 운영 경로의 고정 Gateway와 두 Path 계약은 유지한다.

## 4. TDD 필수 사례

- 307/308 Redirect 응답 뒤 목적지 서버 요청 0건 및 Password·Refresh 전달 0건
- connect/request timeout, Content-Length 초과, chunked 128 KiB 초과 거부
- Login 성공 Projection·Cookie 0건, malformed/unknown field/잘못된 Content-Type/시간 형식 거부
- Refresh 동시 호출 직렬화, 흐름당 최대 1회, 성공 시 원자적 Vault 교체
- Refresh replay·서버 실패·Vault write 실패 시 revoke와 `AUTHENTICATION_REQUIRED`
- revoke 실패 후 `status()`가 authenticated를 반환하지 않으며 재시도 후 Target 부재 확인
- Windows Generic Credential에 corrupt Blob을 실제 기록한 뒤 `read()` 실패와 Target 삭제 확인
- Tauri Command와 Debug·Safe Error·Test Evidence의 Credential/Password 0건
- 기존 LocalStorage Target과 Local Service 회귀 유지

RED를 먼저 재현해 진행 기록에 남긴 뒤 최소 구현, GREEN, 전체 회귀 순서로 진행한다.

## 5. 필수 검증

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
node --test scripts/tests/desktop-tauri-shell.test.mjs
npm run verify:desktop-lint
git diff --check
```

격리 Cargo Wrapper가 신규 Native 계약 테스트를 실행하지 않으면 허용 범위 안에서 Wrapper에 해당 테스트를 포함하고 신규 Native 계약 테스트를 별도로도 실행한다. 장시간 Compile은 한 번만 실행하고 충분히 기다린다. 기존 PostCSS 환경 실패는 원인과 이번 변경 무관성을 분리한다.

## 6. 허용 변경 경로

- `apps/desktop/src-tauri/Cargo.toml`
- `apps/desktop/src-tauri/Cargo.lock`
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/src/native_session.rs`
- `apps/desktop/src-tauri/tests/native_session_contract.rs`
- `scripts/run-isolated-desktop-cargo.mjs`
- `scripts/tests/desktop-tauri-shell.test.mjs`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C05_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-SESSION-C05_completion_report.md`

범위 밖 변경이 필요하면 수정하지 말고 증거와 함께 어울1에게 반환한다.

## 7. 보존·금지

- 사용자 삭제 31건과 미추적 문서 3건, 기존 Web/API/Local Service와 복원된 Windows 파일을 보존한다.
- 실제 Password·Token·운영 Credential·DB·Backup을 사용하거나 기록하지 않는다.
- Public Gateway·Path·공개 API·Tauri Command 이름을 변경하지 않는다.
- Commit·Push·배포·Browser·실제 로그인·설치는 수행하지 않는다.
- 허용 범위 밖 포맷·리팩터링을 금지한다.

## 8. 진행·결과 계약

착수, RED, 각 보정, GREEN, 전체 회귀, 오류·복구, 종료 직전에 시각·단계·상태·변경 파일·명령과 결과·원인과 복구·다음 작업을 진행 기록에 남긴다.

결과는 다음 형식으로 보고한다.

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

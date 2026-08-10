# Windows 최소 파일 복원 검증 요약

- issue_id: `R1-M5-07-WINDOWS-RESTORE-01-I001`
- 기준 Commit: `f2b147ae001621084e8b8fcf1eb671a8b415ca67`
- Branch: `master`
- 승인: 신산님 2026-08-10 두 경로 제한적 복원 승인

## 복원 무결성

| 경로 | HEAD Blob | 복원 후 `git hash-object` | 결과 |
| --- | --- | --- | --- |
| `apps/desktop/src-tauri/src/bin/local-service-lifecycle-host.rs` | `df932af6d5cb76686c78e5288a506139aaf9a3ed` | `df932af6d5cb76686c78e5288a506139aaf9a3ed` | PASS |
| `apps/desktop/src-tauri/tests/fixtures/local-service-error-fixture.mjs` | `6f098b17d4292a0932d087559b24c518de5a7bdf` | `6f098b17d4292a0932d087559b24c518de5a7bdf` | PASS |

- 승인된 두 경로 이외의 restore: 0건
- 복원 파일 내용 편집: 0건
- 남은 tracked 삭제: 31건
- 보존한 미추적 사용자 문서: 3건

## 테스트

| 명령 | 결과 | 범위 |
| --- | --- | --- |
| `node --test scripts/tests/desktop-local-service.test.mjs` | 10 passed, 0 failed, exit 0 | Bridge, fail-closed CSP, guarded Cargo wrapper, headless lifecycle host, sidecar cleanup 계약 |
| `npm run verify:desktop-rust-unit` | 20 passed, 0 failed, exit 0 | Rust lib 16건 + Local Service contract 4건 |

Rust 테스트 1차 샌드박스 실행은 Tauri build-script의 `%TEMP%` permission-file 생성에서 `액세스가 거부되었습니다. (os error 5)`로 종료했다. 동일 소스·설정·명령을 비샌드박스에서 재실행해 전부 통과했으므로 제품·복원 파일 결함이 아니라 실행 권한 경계로 분리한다. 코드·설정 변경은 하지 않았다.

## 정리

- 격리 Cargo target 잔여: 0건
- Cargo/Rustc process 잔여: 0건
- Build·설치·Adapter 구현·서버/DB/Docker 변경: 0건
- 이 결과는 두 파일 복원·관련 회귀 PASS이며 Windows Recovery 실제 여정 또는 M5 Exit PASS가 아니다.

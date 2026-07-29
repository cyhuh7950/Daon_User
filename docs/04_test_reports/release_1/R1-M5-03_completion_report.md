# R1-M5-03-C01 완료보고

## 판정

`COMPLETED` — R1-M5-03의 기존 암호화 저장·설치 동작을 보존하면서 C01의 Metadata, Versioned Header, Windows Handle TOCTOU, Vector 입력, Protocol과 Evidence 계약을 보완하고 전체 검증을 완료했다.

## 판단 이유

- 기존 v1 SQLCipher Schema와 `DAONENC1` 암호문을 additive migration 뒤 재개방했고, 신규 write만 인증된 `DAONENC2` Header를 사용한다.
- 신규 Object는 검증 Content Type, Object Version, Created/Updated UTC, 상태를 기록한다. Area Key는 Wrap Algorithm, Created/Rotated UTC, 상태와 Workspace·Area·Version 복합 식별을 유지한다.
- Windows File read/write/delete는 final Handle의 Root containment·Reparse·Hardlink를 검증하며, Handle rename과 최종 재검증을 사용한다. 실제 Junction과 검사 직후 교체 시나리오가 Root 밖 파일을 사용하지 못했다.
- Vector Put/Search는 `NaN`, ±`Infinity`, Float32 overflow를 모두 `LOCAL_VECTOR_INVALID`로 거부한다.
- Parent/Sidecar Protocol을 함께 `1.1`로 올렸고 legacy·누락·혼합 조합을 fail-close한다.
- 구현 Commit `c21a6b64d9c1a920fb082a4de1b40fd12be63d55` 기준 전체 Gate와 packaged/installed runtime을 통과했다.

## 조치와 결과

### 주요 변경

- Local Store: additive v2 Schema, verified MIME, authenticated v2 Header, legacy read, Key 상태 검증, DB 실패 rollback과 restart orphan recovery
- Windows File Boundary: Win32 Handle ABI, final path/reparse/hardlink 검증, Handle-based atomic rename/delete와 TOCTOU fail-close
- Vector/API: finite Float32 경계와 required content type
- Bootstrap/Credential: Python/Rust Protocol 1.1 동시 변경, owned sensitive buffer best-effort zeroize
- Evidence: 파일 Digest, Check 범위, Runtime, Known Limit, cleanup을 연결한 C01 Manifest

### 검증

- Python 전체: `84 passed`; Win32 ABI 교정 후 전체 10회 연속 PASS
- Ruff: PASS; strict Mypy product source 8 files PASS
- Windows 경로: Hardlink·Junction/Reparse·검사 직후 교체 Race PASS
- Schema/Header: 신규·v1 Upgrade/legacy read·재시작·MIME/Header/Key/Tag/Digest·DB 실패·orphan recovery PASS
- Rust: Unit 16 + Contract 4 = `20 passed`; Clippy 신규 경고 0
- JS Local Service contract: `16 passed`; packaged Sidecar 2-run restart PASS
- Independence: 855 files, violations 0
- Quality Gate: 7 Category, 36 Check PASS, failures 0
- NSIS Installer: 26,391,366 bytes, SHA-256 `43C149E74E4DEF55B1DEC1FD9B3DF86949BBA61ACBC69C8FB4A2004F6910733E`
- 설치 DB: 36,864 bytes, raw header `567031DADC9299762596B2095D26B041`, SHA-256 `C53DA2AD3FE11154150087C811A6655D7C1CFCDC1C090C66C0D0395830D15D50`
- hidden restart DB 불변·External connection 0, Credential 철회 후 재생성 0·Sidecar 시작 0·DB 불변

### Known Limit

- Python immutable `str`/`bytes`, Rust `String`/serde JSON 및 cryptography/OpenSSL 내부 복제는 application code가 완전 zeroize할 수 없다. 소유 가능한 Python `bytearray`와 Rust fixed buffer는 수명 종료·오류·Lock 경로에서 덮어쓴다.
- Legacy `DAONENC1`은 삭제·재암호화하지 않고 read-only 호환한다. 신규 인증 Content Type Header는 `DAONENC2` write부터 적용된다.

## Git·잔여 상태

- 구현 Commit/Origin: `c21a6b64d9c1a920fb082a4de1b40fd12be63d55`
- Evidence Manifest: `docs/03_evidence/release_1/R1-M5-03-C01/manifest.json`
- 정식 `FAILURE_REPORT`: 0회
- 미해결 제품 결함: 0건
- Test 설치·Storage·Credential·App/Sidecar Process·Listener·Installer target·repo generated sidecar/gen/coverage 잔여: 0
- 기존 `%LOCALAPPDATA%/com.daon.user` 보호 Profile: 미변경
- Evidence·진행 기록·이 완료보고는 별도 Evidence-only Commit으로 기록한다.

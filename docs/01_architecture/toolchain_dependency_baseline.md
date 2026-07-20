# Toolchain·Dependency 기준선

## 목적

Release 1 개발·CI·새 환경이 저장소 버전 파일과 Lockfile만으로 같은 Toolchain과 의존성 집합을 재현하도록 정확 버전을 고정한다. `CHG-R1-M1-03-001` C1 기술 정정을 반영하며 제품 계약은 변경하지 않는다.

## 정확 버전

| 영역 | 버전 | 저장소 정본 |
| --- | --- | --- |
| Node.js / npm / Corepack | `24.18.0` / `11.12.1` / `0.35.0` | `.node-version`, `.tool-versions`, `package.json` |
| Python / uv | `3.14.3` / `0.11.2` | `.python-version`, `pyproject.toml`, `uv.lock` |
| Rust | `1.97.1` | `rust-toolchain.toml`, `.tool-versions` |
| Tauri CLI / React Native | `2.11.4` / `0.86.0` | Component manifests, `package-lock.json` |
| PostgreSQL | `18.4` | `.postgres-version`, `.tool-versions` |
| Xcode / CocoaPods | `26.6` / `1.16.2` | `.xcode-version`, `.cocoapods-version` |
| Next.js / React / TypeScript | `16.2.10` / `19.2.7` / `7.0.2` | npm manifests와 Lockfile |

기계 판독 정본은 `toolchain-versions.json`이다. 범위 기호, `latest`, `x`를 사용하지 않는다.

## Workspace 경계

- npm은 `apps/*`, `packages/*`만 소유한다.
- uv는 `services/api`, `services/local-service`만 소유한다.
- 내부 npm Workspace는 `0.0.0` 정확 버전으로 선언하고 같은 버전의 로컬 Workspace로 해석한다.
- Contracts와 Design Tokens는 Runtime 외부 의존이 없는 Leaf다.
- UI는 Contracts·Design Tokens만 직접 의존하며 React는 정확 Peer 계약으로 둔다.
- 승인되지 않은 Framework·Provider·DB Client는 추가하지 않는다.

## 개발자·CI 검증 절차

1. `node scripts/verify-toolchain-baseline.mjs`로 버전 파일·Manifest·Lockfile 일치를 검사한다.
2. npm은 작업 전용 Cache에서 `npm ci --ignore-scripts`를 실행한다.
3. uv는 작업 전용 `UV_CACHE_DIR`·`UV_PYTHON_INSTALL_DIR`에서 Python `3.14.3`과 `uv lock --check`를 사용한다.
4. Rust는 격리 `RUSTUP_HOME`·`CARGO_HOME`에서 `rustc 1.97.1`을 확인한다.
5. 검증 Cache와 Toolchain은 저장소에 넣거나 Commit하지 않는다.

Windows 검증의 격리 Root는 `C:\tmp\daon-r1-m1-03`이다. 이는 개발 증거 수집이며 사용자·운영 제품 절차에 Python·DB CLI를 노출하지 않는다. Package Lifecycle Script는 실행하지 않는다.

## 플랫폼 제한

- Windows Host에는 PostgreSQL Runtime을 설치하지 않았다. 정확 Pin만 검증하며 서비스 설치는 후속 범위다.
- Xcode `26.6`과 CocoaPods `1.16.2` Runtime은 Windows에서 실행할 수 없어 `EXTERNAL_BLOCKED`다. 승인된 macOS Build Host에서 후속 검증한다.
- 사용자 전역 Rust는 변경하지 않는다. 격리 Rustup 경로에서 `1.97.1`을 사용한다.

## 비범위

Framework Source scaffold, 실제 App Build Script, API·DB·Runtime 구현, CI Workflow와 서비스 설치를 만들지 않는다. 실행 코드가 없는 Component에 성공하는 가짜 Build Script도 두지 않는다.

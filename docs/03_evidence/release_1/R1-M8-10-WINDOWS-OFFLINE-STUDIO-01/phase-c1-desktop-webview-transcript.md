# Phase C 메뉴 1 Desktop WebView Actual Gate

- 실행 시각: 2026-08-15T12:30:00+09:00 ~ 2026-08-15T12:51:00+09:00
- 정본: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`
- Branch: `codex/user-auth-screen-split`
- HEAD: `2d4c59e1c761ec12848dcfac8c2f04078dcbb47b`
- 판정: `PHASE_C_MENU_1_DESKTOP_WEBVIEW_PASS`

## 실행 경계

- current source의 Tauri `contract-test` 앱에서 production `WorkspaceSettingsModal`, production 화면 설정 CSS/Token, production Windows Screen Preferences bridge를 사용했다.
- 로그인·Session·사용자 Token을 만들거나 우회하지 않았다. 고정 `Test Notebook` 화면 데이터는 화면 설정 외 데이터 불변 확인만을 위한 명시적 evidence fixture다.
- 외부 Network 요청, API 내부주소, Credential 원문, Password, Secret 출력은 0이다.

## Actual 결과

1. 1920x1080 WebView 최초 실행에서 선택값 `system`, OS 유효값 `dark`, 배율 `100%`를 확인했다.
2. 키보드만으로 설정 Popup을 열고 `light`를 저장해 즉시 밝은 Theme 적용을 확인했다.
3. `dark`를 저장해 즉시 어두운 Theme 적용을 확인했다.
4. 앱을 완전히 종료하고 다시 실행해 초기 paint부터 저장된 `dark`가 복원됨을 확인했다.
5. 실제 Desktop WebView에서 evidence-only `Ctrl+=` 시각 배율 Harness를 사용해 `200%` Reflow와 keyboard 접근성을 확인했다. 이 증거는 Windows 전역 OS DPI 변경이 아니라 WebView 내부 200% 시각 배율 증거다.
6. `Ctrl+0`으로 100%를 복원한 뒤 실제 초기화 버튼을 키보드로 실행해 선택값 `system`, OS 유효값 `dark`로 돌아옴을 확인했다.
7. 모든 단계에서 fixture hash `screen-preference-fixture-v1-0d4cc4f4`, Source `2`, Output `1`이 유지되어 화면 설정이 Notebook 데이터를 변경하지 않음을 확인했다.
8. Tab/Shift+Tab, Home/End, Arrow, Enter, Escape, Alt+F4 경로를 사용했다. 보이는 Console 오류, 내부 URL, SQLSTATE, Stack, Secret 노출은 0이다.

## 증거 파일

- `phase-c1-desktop-system-1920x1080.jpg`
- `phase-c1-desktop-light-1920x1080.jpg`
- `phase-c1-desktop-dark-1920x1080.jpg`
- `phase-c1-desktop-dark-restart-1920x1080.jpg`
- `phase-c1-desktop-dark-200pct-1920x1080.jpg`
- `phase-c1-desktop-reset-system-1920x1080.jpg`

## Cleanup

- evidence Tauri 앱과 이 작업에서 연 설치 앱 Process를 종료했다.
- isolated Cargo target, generated `src-tauri/gen`, 임시 Harness bundle을 제거했다.
- 작업 전 보존한 `apps/desktop/dist`를 exact 복원했다.
- 이 작업에서 시작한 evidence listener 및 관련 Cargo/Rust process 잔류는 0이다. 작업 전부터 실행 중이던 Node listener `127.0.0.1:4174`(2026-08-14 시작)는 관련 없는 보호 Process로 확인해 변경하지 않았다.

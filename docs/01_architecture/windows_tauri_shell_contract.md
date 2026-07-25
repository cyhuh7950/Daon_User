# Windows Tauri Shell 계약

## 범위

`R1-M3-02`는 승인된 공용 React UI를 정적 자산으로 포함하는 Tauri 2 Windows 설치 Shell이다. 실제 Backend·DB·LLM·파일 처리·Local Service·IPC·Loopback은 구현하지 않으며 화면에서는 `deferred_actual` 또는 `unavailable`로 유지한다.

## 런타임·자산

- Rust `1.97.1`, Tauri CLI·crate `2.11.4`, Vite `8.1.5`를 정확 버전으로 사용한다.
- Production `frontendDist`는 `apps/desktop/dist`이며 `devUrl`·원격 Web URL·Dev Server가 없다.
- Windows Bundle은 current-user NSIS이며 R1-M3-02 검증본의 서명 상태는 `unsigned_development`다.
- Product Identifier는 `com.daon.user`, Version은 `0.1.0`, Window Title은 `Daon 사용자 프로그램`이다.

## UI·Navigation

- `packages/ui`, `packages/design-tokens`, `packages/contracts/navigation.json`, `packages/contracts/screens.json`을 직접 사용한다.
- Windows가 허용한 `native_route_key`만 Home·Workspace·Notifications·Account·Organization·Operations 주 탐색으로 제공한다.
- Route Surface를 숨김 유지해 Route 왕복 시 각 공용 UI 내부 상태를 보존한다.
- 600px 이하에서 주 탐색을 여러 줄로 Wrap하고 수평 Scroll을 만들지 않는다. 6개 항목을 숨기거나 삭제하지 않는다.
- 1920×1080 기준, 본문 12px·제목 16px와 공용 UI의 접근성·Tooltip 계약을 유지한다.

## 보안·M3-03 경계

- Capability는 main Window에 연결된 빈 Permission 목록만 사용한다.
- CSP는 Build 포함 자산만 허용하며 `connect-src 'none'`, `unsafe-eval`·범용 `*` 없음으로 고정한다.
- Remote Content, 새 Window, File Drop과 외부 Navigation을 허용하지 않는다.
- Tauri Command·Plugin·Sidecar·Local Service·IPC·Loopback Listener를 추가하지 않는다.
- App Source·Config에는 앱이 정의한 API 절대주소, `localhost`, `127.0.0.1`, Docker Host·Port, `NEXT_PUBLIC_API_BASE_URL`이 없다.

## 검증·후속

- 전용 계약 테스트, Vite Build, locked Cargo Check, NSIS Build, 설치 앱 6개 Route·4개 Window 상태, 전체 순차 회귀, Workspace Lint와 공통 Quality Gate로 검증한다.
- `verify:desktop-type`은 교차 플랫폼 Node Wrapper가 OS Temp 아래 고유 Cargo Target을 만들고 성공·실패 뒤 정확한 Target만 제거한다. 호출자의 수동 `CARGO_TARGET_DIR`은 필요하지 않다.
- `build:desktop-installer`도 저장소 밖 고유 Target을 사용하고 성공 시 Installer Root를 출력한다. Hash·설치 증거 수집 뒤 `cleanup:desktop-cargo -- <정확 Target>`로 해당 Target만 제거한다.
- 실제 Local Service·IPC·Loopback과 App Instance 인증은 `R1-M3-03`에서 별도 구현·검증한다.
- 서명·Update·Rollback은 M9 `TS-OPS-041` 전까지 `deferred`다.

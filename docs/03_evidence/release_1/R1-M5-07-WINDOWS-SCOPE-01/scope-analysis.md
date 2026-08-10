# Windows 설치형 검증 복원 범위 조사 증거

## 조사 기준

- 기준 Commit: `c76d881627e2a740557f22aa9d81700cfdf36267` (`HEAD == origin/master`)
- 조사 방법: 현재 삭제 상태를 유지한 채 `git diff --name-status`, `git ls-tree -r -l HEAD`, `git show HEAD:<path>`, `git grep HEAD`로만 확인
- 금지 준수: 복원·Build·설치·실행·제품/테스트 코드 변경·Stage·Commit·서버/DB/Docker 변경 0건
- 분류 의미
  - `직접 필요`: Windows Local Service의 실제 Manager lifecycle 증거 실행에 직접 입력됨
  - `간접 필요`: 해당 lifecycle의 Windows 부정·회귀 테스트에 필요하지만 설치형 화면 실행 자체의 입력은 아님
  - `이번 증거와 무관`: Mobile 또는 Web 전용이며 Desktop package가 참조하지 않음
  - `추가 확인`: 정적 증거만으로 분류할 수 없는 파일

## 삭제 33건 전수 분류

모든 경로는 HEAD tree에 존재하며 현재 Working Tree에서만 삭제 상태다.

| # | 삭제 경로 | 분류 | HEAD·참조 근거 |
| ---: | --- | --- | --- |
| 1 | `apps/desktop/src-tauri/src/bin/local-service-lifecycle-host.rs` | 직접 필요 | HEAD blob `df932af6...`, 2,837 bytes. `scripts/run-isolated-desktop-cargo.mjs`의 `manager-runtime`이 이 고정 bin을 실행하고 `scripts/tests/desktop-local-service.test.mjs`가 host와 wrapper 계약을 검사한다. |
| 2 | `apps/desktop/src-tauri/tests/fixtures/local-service-error-fixture.mjs` | 간접 필요 | HEAD blob `6f098b17...`, 2,951 bytes. `src/local_service.rs:1824`가 Windows Manager 오류·Retry·Process tree 회귀 테스트 fixture로 직접 읽는다. |
| 3 | `apps/mobile/android/app/src/main/AndroidManifest.xml` | 이번 증거와 무관 | Android App manifest. Desktop package/Cargo/Windows 검증 참조 0건. |
| 4 | `apps/mobile/android/app/src/main/java/com/sinsan/daon/DaonAndroidHostModule.kt` | 이번 증거와 무관 | Android Native Host. Desktop 참조 0건. |
| 5 | `apps/mobile/android/app/src/main/java/com/sinsan/daon/DaonAndroidHostPackage.kt` | 이번 증거와 무관 | Android React Native package. Desktop 참조 0건. |
| 6 | `apps/mobile/android/app/src/main/java/com/sinsan/daon/MainActivity.kt` | 이번 증거와 무관 | Android Activity. Desktop 참조 0건. |
| 7 | `apps/mobile/android/app/src/main/java/com/sinsan/daon/MainApplication.kt` | 이번 증거와 무관 | Android Application. Desktop 참조 0건. |
| 8 | `apps/mobile/android/app/src/main/res/drawable/rn_edit_text_material.xml` | 이번 증거와 무관 | Android drawable. Desktop 참조 0건. |
| 9 | `apps/mobile/android/app/src/main/res/mipmap-hdpi/ic_launcher.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 10 | `apps/mobile/android/app/src/main/res/mipmap-hdpi/ic_launcher_round.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 11 | `apps/mobile/android/app/src/main/res/mipmap-mdpi/ic_launcher.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 12 | `apps/mobile/android/app/src/main/res/mipmap-mdpi/ic_launcher_round.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 13 | `apps/mobile/android/app/src/main/res/mipmap-xhdpi/ic_launcher.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 14 | `apps/mobile/android/app/src/main/res/mipmap-xhdpi/ic_launcher_round.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 15 | `apps/mobile/android/app/src/main/res/mipmap-xxhdpi/ic_launcher.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 16 | `apps/mobile/android/app/src/main/res/mipmap-xxhdpi/ic_launcher_round.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 17 | `apps/mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 18 | `apps/mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_round.png` | 이번 증거와 무관 | Android launcher asset. Desktop 참조 0건. |
| 19 | `apps/mobile/android/app/src/main/res/values/strings.xml` | 이번 증거와 무관 | Android string resource. Desktop 참조 0건. |
| 20 | `apps/mobile/android/app/src/main/res/values/styles.xml` | 이번 증거와 무관 | Android style resource. Desktop 참조 0건. |
| 21 | `apps/mobile/android/gradle/wrapper/gradle-wrapper.jar` | 이번 증거와 무관 | Android Gradle wrapper binary. Desktop build는 npm/Vite/Cargo/Tauri 경로다. |
| 22 | `apps/mobile/android/gradle/wrapper/gradle-wrapper.properties` | 이번 증거와 무관 | Android Gradle wrapper 설정. Desktop 참조 0건. |
| 23 | `apps/mobile/ios/Daon.xcodeproj/xcshareddata/xcschemes/Daon.xcscheme` | 이번 증거와 무관 | iOS Xcode scheme. Windows/Tauri 참조 0건. |
| 24 | `apps/mobile/ios/Daon/Images.xcassets/AppIcon.appiconset/Contents.json` | 이번 증거와 무관 | iOS AppIcon catalog. Windows/Tauri 참조 0건. |
| 25 | `apps/mobile/ios/Daon/Images.xcassets/Contents.json` | 이번 증거와 무관 | iOS asset catalog. Windows/Tauri 참조 0건. |
| 26 | `apps/web/app/api/v1/[...path]/route.js` | 이번 증거와 무관 | Next.js Web BFF route. Desktop package는 `@daon-user/ui`를 직접 사용하고 `apps/web`을 dependency/import하지 않는다. |
| 27 | `apps/web/app/bff/api/[...path]/route.js` | 이번 증거와 무관 | Next.js Web BFF route. Windows Tauri CSP는 `connect-src 'none'`; Desktop build/runtime 입력이 아니다. |
| 28 | `apps/web/app/bff/shell/runtime/route.js` | 이번 증거와 무관 | Next.js Web shell route. Desktop runtime 입력이 아니다. |
| 29 | `apps/web/app/settings/account/page.jsx` | 이번 증거와 무관 | Web page. Desktop는 공용 UI component를 직접 import한다. |
| 30 | `apps/web/app/settings/model-connections/page.jsx` | 이번 증거와 무관 | Web page. Desktop runtime 입력이 아니다. |
| 31 | `apps/web/app/settings/model-connections/provider-settings.css` | 이번 증거와 무관 | Web page style. Desktop runtime 입력이 아니다. |
| 32 | `apps/web/app/settings/organization/page.jsx` | 이번 증거와 무관 | Web page. Desktop runtime 입력이 아니다. |
| 33 | `apps/web/app/workspaces/[workspace_id]/page.jsx` | 이번 증거와 무관 | Web page. Desktop runtime 입력이 아니다. |

분류 집계: 직접 필요 1, 간접 필요 1, 이번 증거와 무관 31, 추가 확인 0, 합계 33.

## 최소 복원 후보와 회귀 위험

### 후보 A — lifecycle host (직접)

- 경로: `apps/desktop/src-tauri/src/bin/local-service-lifecycle-host.rs`
- HEAD 존재: blob `df932af6d5cb76686c78e5288a506139aaf9a3ed`, 2,837 bytes
- 참조: `scripts/run-isolated-desktop-cargo.mjs:155,174`; `scripts/tests/desktop-local-service.test.mjs:128-134`
- 필요한 단계: Local Service sidecar 생성 후 `manager-runtime` 고정 wrapper로 `start → ready → retry → ready → shutdown`을 2회 관찰하고 Secret 출력 0건을 확인하는 단계
- 회귀 위험: 사용자가 삭제한 이유가 확인되지 않았다. 복원하면 사용자 의도와 충돌할 수 있고 Cargo의 자동 bin target 집합이 다시 늘어난다. 반드시 경로 단위 승인 후 HEAD blob 그대로 복원하고 diff를 확인해야 한다.

### 후보 B — error fixture (간접)

- 경로: `apps/desktop/src-tauri/tests/fixtures/local-service-error-fixture.mjs`
- HEAD 존재: blob `6f098b17d4292a0932d087559b24c518de5a7bdf`, 2,951 bytes
- 참조: `apps/desktop/src-tauri/src/local_service.rs:1812-1825,1941-2012`
- 필요한 단계: `verify:desktop-rust-unit`의 Windows-only timeout, invalid protocol, health failure retry, stubborn process tree shutdown, retry race 회귀
- 회귀 위험: 설치 파일에는 포함되지 않지만 테스트가 Node fixture를 실행하므로 임의 수정본은 결과를 왜곡한다. 승인 시 HEAD blob 그대로 복원하고 Hash를 확인해야 한다.

## 삭제 복원 없이 가능한 검증

1. HEAD tree의 33개 파일 존재·blob·크기 확인.
2. package/Cargo/Tauri/공용 UI의 정적 dependency와 참조 그래프 확인.
3. 현재 `apps/desktop/src/desktop-shell.jsx`가 Operations 화면에 `OperationsRecoveryWorkspace`를 전달하지만 `recoveryAdapter`를 전달하지 않는다는 정적 확인.
4. 현재 Tauri command가 `local_service_status`, `local_service_retry` 2개뿐이며 Local Recovery 3개 API를 화면에 연결하는 command/adapter가 없다는 정적 확인.
5. 기존 R1-M5-07 manifest의 자동·서버·Web 증거와 Windows pending 상태 대조.

이 범위는 조사 증거이며 Windows 제품 PASS가 아니다.

## 복원 후에만 가능한 검증 절차

복원은 신산님이 후보 A/B의 정확한 경로를 승인한 뒤 별도 작업지시서에서 수행해야 한다.

1. **복원 무결성**: 두 경로만 HEAD blob으로 복원하고 `git hash-object`가 위 blob과 일치하며 나머지 삭제 31건·미추적 3건이 그대로인지 확인.
2. **정적 회귀**: `node --test scripts/tests/desktop-local-service.test.mjs`로 wrapper·bridge·고정 host 계약 확인.
3. **Windows Rust 회귀**: `npm run verify:desktop-rust-unit`; fixture 기반 timeout/retry/process tree 정리 포함.
4. **Windows 설치 Build**: `npm run build:desktop-installer`; 생성 sidecar 정리와 NSIS 산출물 Hash·서명 상태 기록.
5. **설치·실행 lifecycle**: 실제 Windows 사용자 설치 → 실행 → Operations 클릭 → Local Service `ready` → 재시도 → 종료 후 잔존 Process/Port 0건.
6. **Local 복구 여정**: 전용 Fixture에서 scan → job status → repair 또는 `manual_recovery_required`; 상태·Audit·Trace를 화면/API로 연결하고 평문 Canary 0건 확인.
7. **Cloud Backup/Restore 여정**: 전용 Fixture Workspace에서 목록 → Backup → Preview → 새 Step-up Execute → 결과 확인. 운영 대상·제자리 덮어쓰기·G9 없는 파괴 실행은 0건.
8. **증거 정리**: 화면·Process·Port·API URL/Method/Status·Audit/Trace를 exact Commit·Installer Hash와 연결한다.

## 정적 조사에서 확인된 별도 구현 공백

후보 A/B의 복원만으로 R1-M5-07 Windows 완료 조건 전체를 충족할 수 없다.

- `apps/desktop/src/desktop-shell.jsx:33`은 `OperationsRecoveryWorkspace`에 `clientType="windows"`만 전달하고 `recoveryAdapter`를 전달하지 않는다. 공용 UI는 Adapter가 없으면 "이 Client에는 Recovery API Adapter가 연결되지 않았습니다"를 표시한다.
- `apps/desktop/src-tauri/src/lib.rs`의 Tauri command는 Local Service 상태·재시도 2개뿐이다. 승인된 Local Recovery `scan/status/repair`를 Windows 화면과 연결하는 command/adapter는 정적 참조에서 확인되지 않았다.
- 삭제된 Web BFF route를 복원해도 Desktop package는 이를 import하지 않고 Tauri CSP는 `connect-src 'none'`이므로 Windows 실제 Cloud/Local Recovery 연결 공백을 해소하지 않는다.

따라서 사용자 삭제 복원 승인과 별개로, Windows용 Production-bound `BackupRestoreAdapter`와 Local Recovery Adapter 연결은 제품/테스트 코드 변경 작업지시서가 선행되어야 한다. 이는 이번 조사 작업의 허용 범위 밖이며 여기서 구현하지 않았다.

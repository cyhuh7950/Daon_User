COMPLETED | R1-M3-06-I008 | C32 현재 SHA Quality Gate 안전 진단 Annotation | Allowlist JSON 1행·Fail-close CLI·Job Summary·Workflow·계약 Test·Progress·Attempt 33 | RED 25/27→GREEN 27/27·관련 38/38·Node 308/308·Toolchain·Mobile·Workflow/Bash·Diff PASS; 공통 Gate 기존 Exit 1 보존 | exact-SHA CI 진단 Log·Summary 미확인; 기존 Gate Unit/Independence 실패 잔존 | 어울1의 Commit·Push와 exact-SHA Quality CI 판정

# R1-M3-06 Attempt 33 결과보고

## 판정

C32 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_QUALITY_CI`다. 기존 Quality Gate 판정·Exit Code·Policy·Fallback Evidence·Artifact Upload를 변경하지 않고, Fallback 실행 뒤 현재 `GITHUB_SHA`의 최소 Result 계약을 충족한 경우에만 안전 진단 JSON 1행을 CI Log와 Job Summary에 남기도록 구현했다. failure count는 0이다.

## 판단 이유

- exact Head `992d4679dbc2369d5df5db9356a74eede597ecd4`의 Quality Run `30269316603`은 준비·Toolchain·Lockfile·Desktop Rust 진단을 통과했으나 common Quality Gate만 Exit 1이었다.
- 공개 Annotation이 `Process completed with exit code 1.`만 제공해 실패 Category·Code·Check를 판정할 수 없었고, 인증 제한으로 Artifact 원문을 안정적으로 회수할 수 없었다.
- 정책 완화나 원인 추측 대신, 이미 생성된 현재 SHA Result에서 안전한 식별자만 출력하면 기존 Gate의 판정 권한과 보안 경계를 유지한 채 진단 가능성을 높일 수 있다.

## 조치

### 변경 범위

- `scripts/lib/quality-gate.mjs`
  - `renderCurrentQualityGateDiagnostic({ root, gitSha })`를 추가했다.
  - 기존 `isMinimumQualityGateResult`로 현재 SHA·Schema·Status/Exit·7개 Category 최소 계약을 재사용한다.
  - 유효한 Result는 `CODE`, `overall_status`, `exit_code`, `failures[].category/code/check_id/component`만 JSON 1행으로 출력한다.
  - 식별자는 기존 `safeIdentifier`를 통과하지 못하면 `UNAVAILABLE`로 치환하고, 중복 제거·자전식 정렬·20건 상한을 적용한다.
  - Result 누락·손상·stale SHA·최소 계약 미충족은 고정 `QUALITY_GATE_NO_CURRENT_RESULT`만 출력한다.
- `scripts/verify-quality-gate.mjs`
  - 명시적 `--ci-diagnostic` 모드를 추가했다.
  - 동일 안전 진단 1행을 Console에 출력하고 `GITHUB_STEP_SUMMARY`에 기록한다.
  - Summary 기록 자체의 실패가 원 Quality Gate 판정을 덮지 않도록 진단 경계에서 격리했다.
- `.github/workflows/release-1-quality-gate.yml`
  - 기존 `fallback-evidence` Step 안에서 `--ci-fallback` 뒤 `--ci-diagnostic`을 실행한다.
  - 순서는 `quality-gate → fallback-evidence(Fallback → Diagnostic) → upload-evidence`를 유지한다.
- `scripts/tests/quality-gate.test.mjs`
  - 현재 SHA PASS/FAIL/ERROR, stale/손상/누락, 악성 식별자·비밀값·개행 주입, 다중 실패·중복·20건 상한·결정성, Workflow·Summary 계약을 추가했다.
- Progress와 본 Attempt 33 보고서.
- 미변경: `quality-gate-policy.json`, Root Package/Lock, Toolchain Pin, Product Web/Desktop/Mobile/API/Local Service, iOS/Android, Quality 기본 실행 모드·Exit 의미·Artifact 경로.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C32 RED | Quality 계약 25/27 PASS·2 FAIL: 안전 진단 함수와 Workflow CLI 호출 부재를 예상대로 재현 |
| C32 GREEN | Quality 계약 27/27 PASS |
| Quality·Workflow 관련 | 38/38 PASS |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| Workflow·Syntax | Workflow JSON 2/2, Git Bash iOS Script 3/3, Node Syntax 3/3 PASS |
| 경계 | `git diff --check` PASS; Product·Mobile·Policy·Package·Lock·Toolchain Diff 0 |

### 공통 Quality Gate 판정 보존 근거

- `npm run verify:quality-gate`는 약 5분 11초 실행 후 기존 exact-SHA CI와 같은 `overall_status=FAIL`, `exit_code=1`을 반환했다. 정책·Exit 의미가 진단 추가로 변경되지 않았다.
- 새 CLI는 이 Result에서 정렬된 실패 2건의 `category/code/check_id/component`만 출력했고, Evidence·stdout/stderr·명령·경로·환경값은 포함하지 않았다.
- 전체 Gate의 Unit 실패는 Windows 격리 Cargo 임시 App Manifest 쓰기 권한 거부, Independence 실패는 C31 이전 HEAD의 iOS Test Fixture `PATH_EXTERNAL_ABSOLUTE` 3건이다. C32 변경이 생성한 회귀가 아니며 본 지시 범위에서 수정하지 않았다.
- Gate가 생성한 R1-M1-05 Result·Summary와 `.coverage`는 사전 Hash 기준으로 정확히 원복·정리했다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA Quality Workflow에서 기존 Gate Exit가 그대로 보존되는지, `QUALITY_GATE_CURRENT_RESULT` 또는 `QUALITY_GATE_NO_CURRENT_RESULT`가 Log·Job Summary에 남는지 확인한다.
3. FAIL이면 새 Annotation의 Allowlist 식별자로 실패 Check를 확정하고, Raw Artifact 원문 없이 정책을 추측 완화하지 않는다.
4. C31 이전 Independence Fixture 3건과 Windows Cargo 권한 차단은 C32 범위 밖 잔존 사항으로 어울1이 별도 판단한다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·배포는 수행하지 않았다.

# 수정 작업지시서 `R1-M1-05-C2`

## 판정

`REWORK` · issue_id `R1-M1-05-I001` 유지 · Attempt 1 내부 Correction 2

## 판단 이유

Correction 1은 정확 Toolchain과 Policy Fail-close를 보완했으나, CI에서 Toolchain 준비·검증 또는 `npm ci`가 공통 Gate 전에 실패하면 현재 SHA의 결과 JSON·Summary가 생성되지 않는다. 또한 저장소에 추적된 이전 로컬 PASS Evidence가 Checkout에 존재하므로 `always()` Artifact 단계가 실패한 CI에서도 그 오래된 PASS 파일을 업로드할 수 있다. 이는 “성공·실패와 무관한 현재 후보 SHA 증거” 계약을 위반하고 오판 가능성이 있어 Commit 전 수정한다.

## 조치

1. Checkout 직후 CI 산출물 경로의 기존 `quality-gate-result.json`·`quality-gate-summary.md`를 Ephemeral Runner에서 제거한다. 저장소 파일 자체를 삭제하는 변경은 하지 않는다.
2. Toolchain Pin Load, npm/corepack/uv 준비, 버전 확인, Toolchain 검증, `npm ci`, 공통 Gate 단계에 고정 `id`를 부여한다.
3. 공통 Gate 이후 `if: always()` 단계에서 결과 파일이 없을 때만 현재 `${{ github.sha }}`와 선행 Step Outcome을 포함한 Masked `ERROR/Exit 2` JSON·Summary를 생성한다.
4. Fallback Evidence는 Raw stdout/stderr, Secret, Token, 내부 자격증명 값을 포함하지 않는다. 실패 단계 ID/Outcome만 기록한다.
5. 정상 공통 Gate가 만든 결과 파일이 있으면 Fallback 단계가 덮어쓰지 않는다.
6. Artifact Upload는 Fallback 생성 뒤 `always()`로 실행하고 현재 실행에서 생성된 두 파일만 업로드한다.
7. Workflow Test에 다음 음성·정적 계약을 추가한다: 기존 Evidence 제거가 선행함, 주요 Step ID가 고정됨, Fallback이 `always()`이며 파일 부재 시에만 생성함, 현재 `github.sha`·Step Outcome을 포함함, Upload가 Fallback 뒤임.
8. 가능하면 Workflow의 Fallback 생성 로직을 독립 함수/Script로 Test해 “기존 파일 유지”와 “미존재 시 ERROR 생성”을 실행 검증한다. 별도 Script가 필요하면 원 Work Order의 `scripts/lib`·`scripts/tests` 허용 범위 안에서 최소 구현한다.

## 범위와 완료조건

- 변경 허용: `.github/workflows/release-1-quality-gate.yml`, `scripts/tests/quality-gate.test.mjs`, 필요 시 `scripts/lib/quality-gate.mjs` 또는 `scripts/verify-quality-gate.mjs`, CI 계약 문서, 진행 기록, 로컬 Evidence.
- 변경 금지: Lockfile·Pin·제품 Source·승인 정본·선행 Evidence·Commit·Push·PR·서버 작업. 동일 `npm ci` 반복 금지.
- Test-first로 기존 Workflow가 오래된 PASS Artifact를 남길 수 있음을 Red로 재현하고 수정 후 전체 Test·Toolchain·독립성·7범주 Gate·Hash·Diff를 Green으로 확인한다.
- 진행 기록에 Correction 2 착수·Red·수정·Green·`HANDOFF_READY`를 Append하고 다시 쓰기를 중지한다.

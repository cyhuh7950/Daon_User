# 작업 결과보고서 `R1-M2-02-C02` · Attempt `3`

## 판정

`HANDOFF_READY` · 1920×1080 Browser 증거 교정과 로컬 회귀에 이어 불변 구현 SHA의 GitHub Required Check·Artifact 및 ysna-server ARM64 검증까지 완료했다. S9~S10 Evidence-only 변경을 어울1에게 인계하고 구현·Evidence 쓰기를 중지한다.

## 필수 결과 필드

| 필드 | 결과 |
| --- | --- |
| status | `HANDOFF_READY` |
| issue_id | `R1-M2-02-I001` |
| 수행한 작업 | 기존 Production Build를 실행해 실제 Chrome Viewport를 1920×1080으로 설정했다. Capture 직전 같은 시점에 `window.innerWidth=1920`, `window.innerHeight=1080`, `data-layout-mode=three-pane`, Header Badge `three-pane`, `pane-knowledge`·`pane-conversation`·`pane-studio` 표시, `bottom-tabs` 미존재·Hidden을 확인하고 이미지를 재취득했다. |
| 생성·변경한 결과 | `workspace-1920x1080.png`, `browser-validation.json`, `evidence-manifest.json`, 진행 기록과 본 Attempt 3 보고서를 갱신했다. 새 PNG SHA-256은 `AE3FA1344FEE29831863DB8E52EE21EDF41848111CB6018D283199279FD71EA7`이다. 구현 코드·Package·품질 Gate·1200/800/500 증거는 변경하지 않았다. |
| 테스트 결과 | Browser PNG `1920×1080`, 세 Pane·Badge·Bottom Tab Hidden, Console/API/non-same-origin/금지 Target 0과 로컬 Workspace 14/14·Lint 8 Files를 확인했다. GitHub Run `29810022794`·Job `88568750457` success, Annotation 0, Merge Ref 부모 일치, Artifact PASS·Exit 0·7범주·Failures 0이다. ysna-server ARM64 exact SHA에서 Toolchain·npm ci·Next Build·Workspace 14/14·Lint·공통 Gate 전부 Exit 0, Schema 신호 0·DB 명령 0, Docker 3종 사전·사후 Hash 일치와 임시 자원 0을 확인했다. |
| 미해결 사항 | S9 Evidence 6건은 구현 SHA 이후 생성되어 아직 Evidence Commit SHA와 그 새 Head의 Required Check가 없다. Branch Protection 실시간 인증 조회는 Subagent 권한에서 401이므로 어울1의 인증된 `gh api` 최종 보강이 남았다. 작업 전 R1-M1-04 Dirty 2건은 미수정·미복원 보호했다. |
| 다음으로 필요한 판단 | 어울1이 Evidence-only Diff와 Branch Protection을 검토해 Commit·Push하고 새 Evidence Head를 전달할지 판단한다. 전달 후 같은 어울2가 새 Head Required Check·Artifact를 읽기 전용 재검증한다. |

## 판단 이유

- 재취득 직전 DOM과 Viewport를 같은 Capture 흐름에서 읽었고, 세 Pane의 실제 Bounding Box가 모두 양수이며 `display=block`, `visibility=visible`이었다.
- 저장 직후 PNG Signature `89 50 4E 47 0D 0A 1A 0A`와 Metadata `1920×1080`을 확인했다. Chrome 도구가 반환한 원본 1920×1080 JPEG Buffer는 확대·리사이즈·Crop·시각 편집 없이 PNG로 인코딩했다.
- 저장 파일을 원본 해상도로 다시 열어 좌·중·우 세 Pane과 Header `three-pane` Badge를 직접 확인했다. Attempt 2 이미지에 있던 단일 Pane·Bottom Tab 모순이 제거됐다.
- 기존 다른 Viewport 파일 Hash 4건이 Attempt 2 값과 동일하며, C02 허용 범위 밖 구현·Package·Gate를 수정하지 않았다.
- GitHub Check Run은 구현 Head와 PR #8에 연결된 GitHub Actions App `15368`의 `Release 1 Quality Gate`이며 Annotation 0이다. Artifact의 Git SHA는 검증한 Merge Ref와 일치한다.
- ysna-server는 승인 Root의 exact SHA 전용 경로만 사용했다. 검증 후 Checkout은 detached·clean이고 Docker Container·Network·Volume Hash가 사전과 동일하다.

## 조치

- 현재 상태: `HANDOFF_READY`.
- 구현·Evidence 쓰기: S10 Evidence와 본 보고서·진행 기록 최종화 이후 중지.
- Commit·Push·PR: 어울1 수행.
- 다음 단계: 어울1이 Evidence-only Commit·Push와 Branch Protection 인증 보강을 수행한 뒤 새 Head CI를 읽기 전용 재검증한다.

## 증거

- PNG: `docs/03_evidence/release_1/R1-M2-02/workspace-1920x1080.png`
- Browser JSON: `docs/03_evidence/release_1/R1-M2-02/browser-validation.json`
- Manifest: `docs/03_evidence/release_1/R1-M2-02/evidence-manifest.json`
- 실행 Head: `a074aca92f9ef0b2ecb5a3630dd725a658cd2ec0`
- 불변 구현 Head: `883c36a186b9627f90d5534e66854e5167a7b43b`
- GitHub S9: `docs/03_evidence/release_1/R1-M2-02/github-ci-validation-manifest.json`
- Server S9: `docs/03_evidence/release_1/R1-M2-02/server-validation-manifest.json`

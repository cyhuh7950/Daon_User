# 작업 결과보고서 `R1-M2-03` · Attempt `2`

## 판정

`HANDOFF_READY` · C01의 7개 중대 미진을 TDD로 교정하고 C02에서 800×900 충돌 증거를 정확한 Pixel과 Paint 상태로 재촬영했다. Browser JSON·Manifest·네 PNG·자동 회귀를 최종 정합했으며 이 보고 이후 모든 쓰기를 중지한다.

## 필수 결과 필드

| 필드 | 결과 |
| --- | --- |
| status | `HANDOFF_READY` |
| issue_id | `R1-M2-03-I001` |
| 수행한 작업 | 최초 가중치 계층 오표시, Source–Version–Evidence 계보, Pane 재마운트 Domain 상태, 활성 무동작 Control, 과거 Version, 사실 기반 충돌 판정, Tooltip ARIA를 결함별 Red Test 후 최소 수정했다. C02에서는 코드·Test·설정·Architecture를 수정하지 않고 800×900 충돌 화면 증거만 교체했다. |
| 생성·변경한 결과 | `source-knowledge-controls.js`와 모델·Pane·Workspace 연결, 관련 Test/Lint, 기존 Architecture 계약·진행 기록, Browser Validation JSON, Evidence Manifest, PNG 4건, 본 Attempt 2 보고서가 최종 결과다. 새 Dependency·Lockfile·API·DB·LLM 실행은 없다. |
| 테스트 결과 | Source 16/16 PASS, Workspace 전체 30/30 PASS, Lint 11 files PASS. C5에서 Foundation 8/8, Gate/Independence 43/43, Toolchain, Independence 8 components·10 edges·0 violations, Next Production Build, Common Quality Gate 7범주·Failures 0을 통과했다. C02는 지시대로 C5 Gate·Build를 반복하지 않았다. |
| Browser 증거 | Production Next 서버 HTTP 200에서 Chrome 실제 클릭으로 1920 three-pane, 1200 two-pane, 800 single-pane, 500 bottom-tabs를 검증했다. 4계층 가중치와 Override, 재마운트 보존, 문서·오디오 직접·ASR+LLM Evidence, Recovery Audit, 충돌 3종·최종화 unavailable, Tooltip Click/ARIA/Escape를 확인했다. Console warning/error 0건이다. |
| C02 단일 증거 | 800×900에서 `innerWidth=800`, `innerHeight=900`, `single-pane`, 선택 탭 `충돌`, informational/material/critical 각 1건, disabled 최종화 3건을 확인했다. 1.5초 Paint 대기 후 Clip 캡처했으며 PNG 원본은 정확히 800×900, SHA-256 `EBD30C...5721`이다. 시각검수도 DOM 판정과 일치했다. |
| 미해결 사항 | Browser Sandbox의 Resource Timing 미노출로 정적 Asset URL 목록은 직접 추출하지 못했다. Prototype은 API/DB/LLM 실행 unavailable이며 Browser Source의 금지 URL·직접 `fetch` 0건으로 보완 판정했다. S10 GitHub CI·ysna-server ARM64 검증은 어울1의 Commit·Push와 불변 SHA 전달 뒤 수행한다. |
| 다음으로 필요한 판단 | 어울1이 최종 Diff·Evidence를 검토해 Commit·Push 여부를 판단한다. 불변 SHA가 전달되면 S10 검증을 수행한다. |

## 판단 이유

- React 렌더 Test에서 group·type·default 계층과 Override 추가·해제를 확인했고 실제 Browser에서 같은 상태 전이가 유지됐다.
- `WorkspaceViewState.source_knowledge`가 Tab·등록·Version·RuleSet·충돌·Override·Audit를 소유해 800 폭 Pane 언마운트·재마운트 뒤에도 복원됐다.
- Viewer는 선택 Source·Version의 Evidence Snapshot을 렌더하며 문서 과거 Version, 오디오 직접, ASR+LLM의 ID·위치가 서로 다르게 열렸다.
- 검토 요청·사용 중지는 Audit와 상태를 변경하고 재처리·최종화는 범위 밖 `unavailable`로 비활성화된다.
- 불변 `ConflictPolicyVersion`과 사실 입력이 세 심각도를 결정하며 Seed도 같은 판정 함수를 사용한다.
- 실제 첫 Tooltip Click에서 Focus와 Click 순서 결함을 발견해 Red Test로 재현하고 기존 open-only 전이 계약을 재사용해 수정했다.
- C02 Manifest 6개 항목은 최종 실제 SHA-256과 전수 일치한다.

## 조치

- 현재 상태: `HANDOFF_READY`.
- 구현·Evidence 쓰기: 본 보고와 최종 Progress 기록 뒤 중지.
- Commit·Push·PR: 수행하지 않음.
- 보호 파일: R1-M1-04 두 Dirty 파일은 수정·복원·Stage하지 않음.
- 다음 단계: 어울1 Diff 검토와 Commit·Push 판단.

## 증거

- Browser JSON: `docs/03_evidence/release_1/R1-M2-03/browser-validation.json`
- Evidence Manifest: `docs/03_evidence/release_1/R1-M2-03/evidence-manifest.json`
- 1920: `source-authority-1920x1080.png`
- 1200: `source-processing-1200x900.png`
- 800: `source-conflict-800x900.png`
- 500: `source-status-500x900.png`
- 실행 HEAD: `18afe750d14b0f0c180873b86a0f2c6af54dd408`

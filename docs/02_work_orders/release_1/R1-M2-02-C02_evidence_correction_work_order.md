# 수정 작업지시서 R1-M2-02-C02 · 1920×1080 Browser 증거 재취득

## 0. 판정

| 항목 | 값 |
| --- | --- |
| 원 Work Order | `R1-M2-02` |
| 이전 수정 | `R1-M2-02-C01` |
| 현재 수정 | `R1-M2-02-C02` |
| issue_id | `R1-M2-02-I001` |
| 판정 | `REWORK_REQUIRED` · Browser Evidence 중대 불일치 |
| 집계 | `INCOMPLETE 2/3`, 유효 `FAILURE_REPORT 0회` |
| 실행 | 동일 어울2 · `daon-developer` |

C01의 구현·자동 Test·1200/800/500 Browser 결과는 수락 가능한 상태다. 현재 수정은 1920×1080 Screenshot 의미 정합성과 그 증거 기록만 대상으로 한다.

## 1. 재현된 결함

- `browser-validation.json`은 1920×1080 `three-pane`과 Resize를 기록한다.
- 그러나 Hash가 고정된 `workspace-1920x1080.png`는 `bottom-tabs` 배지, 상단 Pane Switcher와 자료 Pane 하나만 표시한다.
- Manifest Hash는 파일과 일치하지만 Screenshot 의미가 JSON·결과보고와 모순되므로 1920 Browser PASS를 인정할 수 없다.

## 2. 필수 조치

1. 기존 Production Build로 실제 Next Production Process를 실행한다. 구현 코드는 수정하지 않는다.
2. Chrome Viewport를 1920×1080으로 설정하고 다음을 같은 Capture 직전에 실제 DOM에서 확인한다.
   - `window.innerWidth === 1920`, `window.innerHeight === 1080`
   - `data-layout-mode === three-pane`
   - `pane-knowledge`, `pane-conversation`, `pane-studio` 세 면이 모두 표시됨
   - `bottom-tabs`가 표시되지 않음
   - Header Layout Badge가 `three-pane`
3. 위 DOM 확인이 유지되는 동일 시점에 `workspace-1920x1080.png`를 다시 저장한다. Viewport Reset 뒤 또는 다른 폭 상태에서 Capture하지 않는다.
4. 저장 직후 PNG 실제 Pixel Dimension이 `1920×1080`인지 확인한다.
5. Screenshot을 다시 열어 세 면과 `three-pane` 배지를 시각 확인한다. 이미지 내용 확인 전 PASS를 기록하지 않는다.
6. `browser-validation.json`의 1920 항목·Capture 시각·실제 DOM 값, `evidence-manifest.json` Hash를 새 파일 기준으로 갱신한다.
7. 기존 1200·800·500 Screenshot Hash와 구현·Test 파일은 변경하지 않는다.
8. Console Error 0, API 요청 0, non-same-origin 0, 금지 Target 0을 1920 Capture Session에서 다시 확인한다.

## 3. 검증과 보고

- `npm run verify:workspace` 14/14와 `npm run lint:workspace`를 재확인한다.
- `git diff --check`, 추적 삭제 0, Lockfile·M2-01 정본 불변, 기존 R1-M1-04 Dirty 2건 보호를 확인한다.
- 진행 기록에 `C02_EVIDENCE_RECAPTURE` 단계를 남긴다.
- Attempt 1·2 보고서는 사실 기록으로 보존하고, `docs/02_work_orders/reports/R1-M2-02_attempt-3.md`에 결과를 작성한다.
- 결과보고에는 PNG Pixel Dimension, DOM Layout Mode, 표시 Pane 3개, Bottom Tab Hidden, 새 SHA-256을 명시한다.
- 완료 후 `HANDOFF_READY`를 제출하고 구현·Evidence 쓰기를 중지한다.

## 4. 금지

- Screenshot을 확대·리사이즈·편집하여 1920×1080으로 만드는 행위를 금지한다.
- JSON만 수정하거나 기존 잘못된 PNG를 재사용하지 않는다.
- 구현 코드·Package·품질 Gate·다른 Viewport 증거를 수정하지 않는다.
- 실패한 Capture를 PASS로 기록하지 않는다.

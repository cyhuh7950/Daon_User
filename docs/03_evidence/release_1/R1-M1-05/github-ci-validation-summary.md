# R1-M1-05 GitHub CI·PR·Branch Protection 검증 요약

## 판정

`GITHUB_INTEGRATION_GATE_PASS` · 외부 `BLOCKED` 해소 · 현재 인계 상태 `HANDOFF_READY`

GitHub 실제 CI, PR Required Check, PR merge ref Artifact와 `codex/release-1` Branch Protection을 확보했다. 이 문서 추가 Commit 뒤 새 Head에서 Required Check를 다시 실행해야 하므로, R1-M1-05의 최종 `COMPLETED` 수락은 어울1 판단으로 남긴다.

## 검증 대상

- Repository: `cyhuh7950/Daon_User` · `PUBLIC`
- Public 전환: 신산님이 옵션 2를 승인하고 전환 완료
- Draft PR: [#6](https://github.com/cyhuh7950/Daon_User/pull/6)
- Base / Head: `codex/release-1` (`707871b...b39e`) / `codex/r1-m1-05` (`471020f...0c3`)
- 검증 Run / Job: [Run 29762258282](https://github.com/cyhuh7950/Daon_User/actions/runs/29762258282) / [Job 88419490913](https://github.com/cyhuh7950/Daon_User/actions/runs/29762258282/job/88419490913)

## CI·Artifact 결과

| 항목 | 결과 |
| --- | --- |
| Workflow / Event | `Release 1 Quality Gate` / `pull_request` |
| Run / Job | `success` / `success` |
| Job 시간 | `39s` (`17:04:29Z`~`17:05:08Z`) |
| 주요 단계 | Checkout, stale Evidence 제거, 승인 Toolchain 준비·검증, `npm ci`, 공통 Gate, Fallback 확인, Artifact Upload 모두 `success` |
| Artifact | ID `8469274296`, `release-1-quality-gate-7835a4ef679080766657249016ffcedab23499a9` |
| Result Hash | `F572A9ED8BD6145AC9A16F8343B56309E1580362D1FB61E8737BDE32E0E8F1BB` |
| Summary Hash | `92A1FAE96ECC84E290EBDC5C27F39093EF9F35CAFE20C203C70AAFA7215817CD` |
| Artifact 계약 | `git_sha=7835a4ef...99a9`, `PASS`, Exit `0`, 7 Categories, Failures `0` |

`7835a4ef...99a9`는 GitHub가 만든 PR merge ref Commit이다. 부모는 Base `707871b...b39e`와 Head `471020f...0c3`이므로 Artifact는 단순 Head가 아니라 실제 병합 후보를 검증했다.

## Merge 차단 계약

- PR `mergeStateStatus`: `CLEAN`
- Required Check: `Release 1 Quality Gate` · `SUCCESS` · GitHub Actions App ID `15368`
- `codex/release-1` 보호 규칙:
  - Strict Required Status Checks `true`
  - Required Context/Check `Release 1 Quality Gate`
  - Admin 적용 `true`
  - Force Push 허용 `false`
  - Branch 삭제 허용 `false`

따라서 현재 검증 후보에서 Required Check와 Branch Protection이 모두 적용되어 있다.

## 서버 검증과의 분리

- ysna-server Evidence는 `3b0f03fec28fd545b34130c1a0c6fae68efeda15`의 ARM64 격리 서버 검증이다.
- GitHub Evidence는 PR #6의 Head와 GitHub merge ref에 대한 CI·Merge 차단 검증이다.
- 두 검증은 서로 대체하지 않으며 각각의 Evidence를 유지한다.

## 경미 위험

GitHub API는 동일한 Node.js 경고를 두 Annotation으로 반환했으나 고유 내용은 1건이다. `actions/checkout@v4`, `actions/setup-node@v4`, `actions/upload-artifact@v4`가 Node.js 20을 대상으로 하며 Runner가 Node.js 24로 강제 실행했다는 경고다. 현재 Run은 성공했고 Gate 결과를 깨지 않으므로 경미한 비차단 위험으로 분류한다. 구현은 수정하지 않았으며 다음 Work Order 흡수 후보로 남긴다.

## 검증 한계와 다음 Gate

- 공개 API로 Repository·PR·Run·Job·Artifact 메타데이터·merge ref 부모·Check Run·Annotation·보호 Branch의 Required Context/App을 재검증했다.
- Artifact 파일 Hash/내용과 Branch Protection의 Strict·Admin·Force Push·Deletion 세부값은 전달받은 인증 API Snapshot과 공개 API 결과를 교차 대조했다. Token·원문 Log·개인정보는 저장하지 않았다.
- 이 Evidence 문서 자체가 새 Commit을 만들면 검증 Head가 바뀐다. 어울1이 Commit 후 새 Head에서 동일 Required Check를 재실행·확인해야 최종 수락할 수 있다.

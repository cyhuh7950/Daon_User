# R1-M2-02 GitHub CI 검증 요약

- 구현 SHA: `883c36a186b9627f90d5534e66854e5167a7b43b`
- PR: `#8`, Draft/Open, Base `codex/release-1`
- PR Merge Ref: `2f668135f31d6b6297354bc99c528b4c5451a598`
- Merge Ref 부모: Base `863261a0ecb506816e57c0922ec2f7d5c1eb142a`, Head `883c36a186b9627f90d5534e66854e5167a7b43b`
- Required Check `Release 1 Quality Gate`, GitHub Actions App ID `15368`: Run `29810022794`, Job `88568750457`, `completed/success`
- Annotation: Check output `0`, annotations endpoint `[]`, Node.js 20 Deprecated 고유 건수 `0`
- Artifact `8487038910`: ZIP SHA-256 `BC6B8B14ADBAD25B2C936BA0BE357C416F0F38CB644947D38068EB4A854E2E54`, 2개 파일
- Artifact 계약: Merge Ref 일치, `PASS`, Exit `0`, 7범주 전부 `PASS`, Failures `0`

Branch Protection은 승인된 R1-M1-05 기준선의 `strict=true`, Required Context/App ID, `enforce_admins=true`, Force Push/Delete false와 현재 Check Context/App ID가 일치한다. 현재 Subagent 연결은 Repository pull-only라 Protection REST의 실시간 인증 조회가 401이었으며, 어울1이 Evidence Diff 최종 검토에서 인증된 `gh api`로 보강한다.

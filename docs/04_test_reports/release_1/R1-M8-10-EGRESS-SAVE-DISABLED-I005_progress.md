# R1-M8-10-EGRESS-SAVE-DISABLED-I005 진행 기록

## 2026-08-21 착수·영향 검토

- 정본 Root는 `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, Branch `codex/user-auth-screen-split`, origin `git@github-cyhuh7950:cyhuh7950/Daon_User.git`, HEAD `7e9378c59b9bd714916126ee42a758a109b037e0`, staged0이다.
- 운영 화면 증상은 정책 값이 이미 목표와 같을 때 reducer `loaded`가 `canSave=false`로 고정되어, 사용자가 현재 비밀번호를 입력해도 저장 버튼이 disabled인 것이다.
- 변경 전 회귀 위험은 parent deny인 Workspace가 allow 정책을 재저장해 조직 차단을 완화하는 것, 비밀번호/Step-up 값이 DOM·state에 남는 것, 중복 submit이다. 공개 API·정책 의미·ACL·Step-up 경계는 변경하지 않는다.
- 기존 테스트된 코드 변경 사유와 전후 차이: 변경 전 `loaded → canSave=false`, draft change 뒤에만 parent/scope 판정을 수행했다. 변경 후 `loaded/drafted → 동일 canSaveDraft(parent,specific scope,current draft)`이며, 버튼은 `canSave && passwordPresent && !saving`일 때만 활성화되고 submit 함수도 같은 조건을 재검증한다.

## TDD RED→GREEN

- RED `10 PASS / 2 FAIL`: same-policy loaded projection의 `canSave` 기대 true가 실제 false였고, password input 후 저장 버튼 disabled 기대 false가 실제 true였다. 기존 parent deny·Step-up·password clear 테스트는 PASS였다.
- GREEN: 현재 organization/workspace draft를 변경하지 않아도 scope 정책상 유효하면 저장 가능하다. parent deny Workspace의 allow 완화는 계속 false, deny 재저장은 true다.
- 비밀번호는 uncontrolled ref에 유지하면서 presence boolean만 React state에 둔다. scope 전환과 요청 finally에서 원문 input과 presence를 모두 지우며 저장 버튼은 다시 disabled다. 저장 함수는 실제 current password가 없거나 saving/canSave false이면 adapter를 호출하지 않는다.
- same-policy Workspace 저장은 기존 exact Workspace ETag·Step-up adapter를 호출했고 password 원문은 adapter와 DOM에서 finally clear됐다. 조직/Workspace 별도 단계, ACL, parent deny, same-origin BFF는 변하지 않았다.

## 검증·종료

- focused Egress React/API/BFF `13/13 PASS`.
- 제품 lint `2 files PASS`. 테스트 파일까지 boundary lint에 넣은 첫 명령은 의도된 absolute URL negative fixture를 1건 검출했으며, 제품 파일 정식 lint는 통과했다.
- Web production build·TypeScript·12 static pages·product boundary `391 files / violations0 / boundaryErrors0` PASS.
- `git diff --check` PASS, staged0. 기존 Mobile/model-connections 삭제와 다른 dirty/untracked는 restore/delete/stage하지 않았다.
- 실제 운영 비밀번호·정책 write·commit·push·배포는 0이다. 운영 반영에는 별도 승인된 exact commit/push와 Daon Web 배포가 필요하다.

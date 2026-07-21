# R1-M2-01 GitHub CI 검증 요약

- 구현 SHA: `c471fad58f124e3ad28e33d98486f139306c0d91`
- PR: `#7`, Draft, Base `codex/release-1`
- PR Merge Ref: `10886238c8869abc8948a805233a8b41a9b4f12f`
- Merge Ref 부모: Base `36a0b5b6e0a2f2b1c3125ffa76089be00eb790b0`, Head `c471fad58f124e3ad28e33d98486f139306c0d91`
- Branch Protection Required Check: `Release 1 Quality Gate`, App ID `15368`
- Run `29799417719`, Job `88537344839`: `completed/success`
- GitHub Annotation: `annotations_count=0`, annotations endpoint 빈 배열, Node.js 20 Deprecated Annotation 고유 건수 `0`
- Artifact `8483191783`: ZIP SHA-256 `4151ECDE194BEF9B554AC1B15EC33E7EFC6C58AACA62D02BBBBBE7AAB5EF91D1`, 2개 파일
- Artifact 계약: Merge Ref 일치, `PASS`, Exit `0`, 7범주 전부 `PASS`, Failures `0`

Job 원문 로그에는 Node.js 24 실행 중 일반 deprecation 문자열이 있으나 GitHub Check Annotation으로 게시된 항목은 없다. 작업지시서의 판정 대상인 Node.js 20 Deprecated Annotation은 0건이다.

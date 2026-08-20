# R1-M8-10-EGRESS-POLICY-LOCK-I003 진행 기록

## 2026-08-21 착수

- 정본: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, Branch `codex/user-auth-screen-split`, origin `git@github-cyhuh7950:cyhuh7950/Daon_User.git`, HEAD `d2ae9c42ce51aa5aac562fc623e8eb47c8144240`, staged0.
- 운영 root cause: `PostgresEgressPolicyRepository._select_current(for_update=True)`의 bare `FOR UPDATE`가 JOIN의 immutable `egress_policy_versions`까지 lock 대상으로 삼아, SELECT+INSERT만 가진 restricted app role에서 SQLSTATE 42501을 발생시킨다.
- 변경 전/후: JOIN 전체 `FOR UPDATE` → mutable current binding alias만 `FOR UPDATE OF binding`. Version UPDATE/DELETE 권한이나 수동 GRANT는 추가하지 않는다.
- 보호: 공개 API/data/security 변경0, 외부 policy/provider write0, commit/push/deploy0, 다른 dirty/untracked 미접촉.
- 다음: static SQL exact RED 고정 후 최소 GREEN, disposable actual PostgreSQL restricted role Gate.

## 2026-08-21 RED → GREEN · actual PostgreSQL

- RED: static contract `1/2 FAIL`로 bare `FOR UPDATE`를 확인했다. 최소 제품 변경은 suffix를 `FOR UPDATE OF binding`으로 한정한 한 줄이다. static contract는 `2/2 PASS`로 전환됐다.
- actual Gate 1차: migration·seed와 제품 create/replay/concurrency는 진행됐으나 immutable negative가 driver `PostgresError`가 아니라 저장소의 safe `CloudDatabaseError(DATABASE_ACCESS_DENIED)`로 변환되어 harness assertion이 실패했다. 제품 오류가 아니며 DB/role은 trap으로 각각 0 정리됐다.
- 복구: harness가 public safe DB error code를 검증하도록 좁게 교정하고 새 고유 DB/role로 1회 재실행했다.
- actual PASS: migration head `0020`; restricted role은 versions `SELECT+INSERT=true`, `UPDATE+DELETE=false`, bindings `SELECT+INSERT+UPDATE=true`. Organization `create_and_activate` 성공1, 동일 key replay duplicate0, 2-thread 동시 요청은 current binding1·idempotency record2를 유지했다. Versions UPDATE/DELETE는 underlying SQLSTATE42501이 `DATABASE_ACCESS_DENIED`로 fail-close됐다.
- cleanup: disposable DB0, role0. 운영 DB·공용 role·외부 policy/provider write0.
- fresh 회귀: Egress focused `12/12 PASS`, Python compile, shell syntax, OpenAPI `75 paths / 94 operations / 120 schemas / 31 errors` exact PASS.
- 변경: `egress_policy_postgres.py` 한 줄, static contract, actual PG gate Python/shell, 본 Progress. 공개 API/data/security 및 권한 grant 변경0.

## 2026-08-21 REWORK1 · distinct-write concurrency

- Reviewer finding: 기존 actual concurrency는 동일 idempotency key라 advisory key 직렬화만 검증했고, 서로 다른 write가 current binding row에서 경쟁하는 경로는 실행하지 않았다.
- RED 목표: 서로 다른 idempotency key·서로 다른 valid payload·동일 stale ETag를 2-thread barrier로 동시에 제출한다. 정확히 1개만 성공하고 loser는 safe `VERSION_CONFLICT`; current1, version/binding append1, success idempotency1, loser write0이어야 한다.
- actual RED: 1개 성공·1개 error까지 도달했지만 loser가 `VERSION_CONFLICT`가 아니었다. 잠금 대기 중 기존 binding이 `current=false`가 되어 locking SELECT가 `None`을 반환하고 `_view(None)`이 unavailable로 분류하는 경로를 확인했다. cleanup DB0·role0.
- 최소 GREEN: locking SELECT가 `None`일 때만 current를 nonlocking 재조회한다. 최신 ETag가 요청의 stale ETag와 다르면 `VERSION_CONFLICT`를 반환하며 binding lock과 restricted privilege는 완화하지 않는다.
- 첫 GREEN 재실행은 제품 경쟁 assertion(`success1 + VERSION_CONFLICT1`)을 통과한 뒤 count SQL의 psycopg literal `%` escape 누락으로 harness가 실패했다. 제품 오류가 아니며 cleanup DB0·role0; `LIKE ... %%`로 교정했다.
- actual 최종 PASS: same-key replay duplicate0 유지. distinct-key/payload 경쟁은 success1·`VERSION_CONFLICT`1·loser write0, current binding1, policy version/binding append1, success idempotency1이다. Restricted privilege와 immutable versions UPDATE/DELETE denial도 유지했고 migration head0020, cleanup DB0·role0다.
- fresh 회귀: Egress focused `12/12 PASS`, static contract `2/2`, Python compile, shell syntax, OpenAPI exact PASS. 외부/운영 write·commit/push/deploy0.

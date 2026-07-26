> 어울1 재분류: `INCOMPLETE` — 반복 재현되는 Fixture Marker 14개와 Cleanup 완료 증거 불일치로 Attempt 4 직접 구현에 대체됨.

COMPLETED | R1-M3-03-I001 | Shutdown Spawn Race·Bounded Job 종료·실제 Manager 오류 경로 보정 | Rust Manager·Windows Fixture·Test·Evidence | 전용 RED→GREEN·오류 Fixture 5종·공통 7범주 Gate PASS | Dirty Snapshot이므로 exact implementation commit Gate는 어울1 후속 | Diff·Evidence 재검토 요청

# R1-M3-03 Attempt 3 작업보고

## 판정

`COMPLETED` — C02의 Important 3건과 문서·증거 정합을 코드, 결정론적 Test, 실제 Windows Child/Descendant Fixture 및 최종 Dirty Snapshot 전체 Gate로 보정했다.

## 판단 이유

- Generation-bound Spawn Permit과 Spawn Gate가 Shutdown 상태 기록과 실제 Process Spawn 사이를 원자화한다. Retry의 이전 Service Stop이 지연되거나 Start가 Spawn 직전에 예약된 경우에도 Shutdown 이후 새 Process를 만들지 않는다.
- Runtime Mutex와 Spawn Gate는 상태 원자화 직후 해제된다. Process Start/Stop I/O를 기다리는 동안 Manager Lock을 보유하지 않는다.
- `TerminateJobObject` 반환값을 검사한다. EOF/자연 종료, Job 종료, 직접 Child Kill Fallback의 각 단계는 독립된 제한시간으로 Polling하며 무제한 `Child::wait()`를 사용하지 않는다.
- Job 종료 실패, Job 성공 뒤 Child 잔존, 직접 Child Kill 실패, 최종 Wait 실패를 Injectable Windows API·Clock 경계로 검증하고 안정 오류를 반환한다.
- 독립 Node Fixture를 실제 `LocalServiceManager → RealServiceLauncher → ManagedService.stop → stop_child` 경로로 실행했다. 오류 Fixture는 `job.terminate()`를 직접 호출하지 않는다.
- No Ready, Invalid Ready, Post-ready Health 실패, EOF 무시 Descendant Listener, Retry/Shutdown Race 3회에서 기대 상태·오류와 Parent·Descendant·Listener 0을 확인했다.
- Invalid Ready와 Health 실패 후 Retry는 각각 새 Credential로 `ready`에 복구했다.

## RED→GREEN 근거

| 범위 | RED | GREEN |
| --- | --- | --- |
| Retry Stop 지연 중 Shutdown | Shutdown 후 Launch Count `2` | Launch Count 불변 `1` |
| Spawn 직전 Shutdown | 예약 해제 뒤 Process Spawn 발생 | Process Spawn `0` |
| Spawn Gate I/O Scope | Blocking Stop 중 Gate 획득 실패 | 상태 원자화 직후 Gate 획득 가능 |
| Job 종료 | Injectable 종료 경계·함수 부재 | 실패·잔존·Kill/Wait 오류 4 Case PASS |
| 실제 Manager 오류 경로 | Rust Test Harness stdout가 Ready를 오염 | 독립 Fixture로 5종 Production 경로 PASS |

## 검증 결과

| 항목 | 결과 |
| --- | --- |
| Rust Manager·Job·실제 Fixture Unit | 14/14 PASS |
| Rust Contract | 3/3 PASS |
| Python Bootstrap/Auth Target | 19/19 PASS |
| Frontend Target | 10/10 PASS |
| Runtime Verifier Unit | 3/3 PASS |
| Source Manifest Unit | 1/1 PASS |
| Desktop Production Build | 43 modules, PASS |
| 실제 오류 Fixture | 5종 PASS, Retry/Shutdown Race 3회 |
| 종료 후 Fixture Process·Listener | Parent 0, Descendant 0, Listener 0 |
| 공통 7범주 Gate | Overall PASS, Exit 0, Failures 0 |

## 최종 전체 Gate

- 명령: `node scripts/verify-r1-m3-03-quality-gate.mjs`
- 실행 주체: npm Registry·OSV Metadata Egress가 승인된 어울1 상위 Context
- 결과: lint 6, type 3, unit 7, contract 2, build 6, security 3, independence 1 모두 PASS
- Policy SHA-256: `767EEE2BB7142BCEECF94DF32674AE9EB2A789D71B83B699A0303C31EC8323D2`
- Result: 48,312 bytes, SHA-256 `0CD09609627542A672077ECFA80A1D740006953F4C339947442639EA207ADED4`
- Summary: 516 bytes, SHA-256 `42414A0B287028FE7A36245D4F3A9F2E9821DE7E8A4C9BDD686325DB1C182BE3`
- Gate Git SHA `bd13f1694623ca1225415c0f157ef88f94df6f38`은 `dirty_snapshot=true`의 Base HEAD이며 exact 구현 Commit SHA가 아니다.

## Source·증거 결속

- `source-manifest.json`: Source 44개, Base HEAD `bd13f1694623ca1225415c0f157ef88f94df6f38`
- Source Entries SHA-256: `81E04EEF3E5DAA5E3202094F72AE61FDAB6E9B0205761A4ECCE113CB4E0D3EFF`
- Tracked Binary Patch SHA-256: `880E9902290D4D823183DC136C248D9BBC699AABB856F28BEEC3B1A8AFFBAB59`
- C01·C02 Progress는 최종 Evidence Manifest에 Byte·SHA-256으로 결속한다.
- Rust Host Evidence는 `public_host_evidence_contains_secret_fields:false`로 실제 공개 상태 Source·Evidence 범위만 주장한다. Packaged Sidecar 출력 검사는 별도 Runtime Evidence를 따른다.
- Attempt 1·2 원문은 보존하고 어울1의 `INCOMPLETE` 재분류와 후속 Attempt 대체 배너를 추가했다.

## 변경·후속 경계

- 변경은 Rust Manager/종료 Test Seam, Windows 실제 오류 Fixture, C02 문서·증거에 한정했다.
- Browser Loopback/Port/Token 비노출, same-origin/Network Fail-close, 승인 Protocol과 데이터 계약은 변경하지 않았다.
- Commit·Push·PR·Merge·SSH·서버 배포·GUI·DB Migration은 수행하지 않았다. DB Migration은 `N/A`다.
- Exact Implementation Commit Gate, Windows 실제 설치 GUI 수명주기와 ysna-server exact-SHA 검증은 어울1 후속이다.

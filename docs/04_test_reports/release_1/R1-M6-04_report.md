# R1-M6-04 결과보고서

## 판정

`COMPLETED` (내부 계약·자동 검증 범위). 외부 실제 장치와 Relay 서버 검증은 미실행이다.

## 판단 이유

- Pairing이 tenant/device identity와 온라인 상태, 공개키·인증서 digest·만료 시각을 생성한다.
- 인증서 회전 시 세대가 증가하고 이전 digest는 `CERTIFICATE_INVALID`로 거부된다.
- relay authorization은 `outbound`만 허용하며 `inbound`는 `PUBLIC_INBOUND_FORBIDDEN`으로 차단한다.
- revoke 후 인증서 검증과 relay authorization이 `DEVICE_REVOKED`로 실패한다.
- `services/api/tests/test_local_node_relay.py` 4개가 통과했고 기존 API 전체 167건 중 25건은 기존 skip, 실패 0건이다.
- 변경된 코드에는 외부 URL·브라우저 호출·비밀 원문 로깅·공개 HTTP API가 없다.

## 조치

- 구현과 테스트를 기준 저장소에 커밋한다.
- 실제 장치 keychain, 외부 Relay, 서버 배포 검증은 R1-M6-04 후속 통합·운영 테스트에서 별도 수행한다.
- 다음 작업은 계획의 선행 의존성을 확인한 뒤 진행한다.

## 실행 증거

```text
$env:PYTHONPATH='src'; uv run python -m unittest tests.test_local_node_relay
Ran 4 tests ... OK

$env:PYTHONPATH='src'; uv run python -m unittest discover -s tests -p 'test_*.py'
Ran 167 tests in 11.575s
OK (skipped=25)
```

`R1-M6-04_progress.md`에 단계별 복구 기록을 보존했다.

# Route 모델·기본 Provider 메인 QA 기록

- 담당/기준: 2026-10-04, 어울. 작업 브랜치 `codex/next-user-development`, 시작 HEAD `3de829c856343672b265d56e15f6575defefe156`.
- 범위: OmniRoute 모델 조회·선택·생략 시 `auto`, Anthropic/OpenAI/Gemini의 개인 Key·모델 없는 사용 대기 등록. WSL QA URL `http://172.27.253.53:3330/`; Oracle·ysna·main 제외.
- 현재 상태: 로컬 구현·독립 검증 완료, WSL QA 전환 준비. WSL 새 배포·세 연결 등록은 아직 수행하지 않음.
- 독립 관련 검증: 마지막 리뷰 재작업 후 Python 9개 파일 `223 passed, 18 subtests passed`, 기존 httpx 경고 19건. Node 화면/BFF 3개 파일 `63 passed`. Web production build·TypeScript·Web UI boundary 475파일 위반 0건, 전체 product UI boundary 498파일 위반 0건, `git diff --check` 통과.
- 전체 API 검증: 별도 WSL SSH 경로에서 `863 passed, 44 skipped, 11 failed, 202 subtests passed`. 10건은 기존 라이선스 fixture의 `generation_runs` 등 현재 허용 자원과 불일치하며 이번 변경 파일이 아니다. 1건은 `test_runtime_http.py`의 0.15초 이벤트루프 시간 한계로 WSL 부하 상태에서 0.207초가 나왔고 단독 재실행도 0.207초로 실패했다. 전체 API PASS로 표기하지 않는다.
- 임시 테스트 자원: `/tmp/daon-user-route-full-qa-20261004-001`을 테스트 후 정확한 경로 확인, 삭제 및 잔류 0 검증. 테스트 결과 임시 파일은 복구하지 않는다.
- WSL 읽기 전용 사전 점검: 기존 연결 8개. ID/약어 `AN`, `OA`, `GE` 충돌 없음. OmniRoute `provider-omniroute` 허용 모델 0개. Workspace 기본 모델 2개. 현재 API/Web Compose 출처는 `/home/daon/deploy/daon-user-provider-89de2e95`, 이미지 ID API `3f64f29e...`, Web `9b8be362...`; 기존 컨테이너 healthy. 3330 화면 HTTP 200 및 로그인된 관리자 목록 8개를 관찰했다.
- WSL 실행 경로: 직접 `wsl.exe`는 `Wsl/Service/0x8007274c`로 간헐적 실패했지만 설정된 `WSL-server` SSH 별칭은 정상. WSL 기존 Secret 경로를 참조하는 Compose `config --quiet` 통과. Secret 원문은 읽거나 출력하지 않았다.
- 배포 전 gate: 독립 코드 리뷰의 legacy OmniRoute 설정 변경·`auto` 보존 회귀 및 pending CUSTOM Key UI 문제는 최신 diff 재검토에서 해결 판정. 정기 health는 60분마다 Provider 생성 probe를 호출할 수 있어 비용 경계 미해결이다. 기존 WSL OmniRoute Endpoint는 컨테이너 내부에서 닿지 않는 `localhost:20128/v1`이며 승인 없는 Endpoint 변경과 실제 유료 호출은 하지 않는다. 현재 QA DB의 허용 모델 0개·미접속 Endpoint를 유지하는 QA-only 코드 배포와 운영 병합을 분리하며, 운영 병합은 비용 정책 전까지 보류한다.
- 다음: 재작업 독립 검토 및 집중 테스트 → scoped commit/push → WSL exact-SHA 격리 checkout/API·Web QA 배포 → 기존 8개 보존과 세 기본 연결 등록·화면 확인. 전체 gate의 기존 실패와 미검증은 남긴다.

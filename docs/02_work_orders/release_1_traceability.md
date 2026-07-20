# Release 1 설계·작업·테스트 추적표

## 추적 규칙

모든 구현 계약은 `설계 조항 → 결정 → Work Order → 사용자 여정 → 테스트 → 증거/Gate`로 연결한다. 범위 ID는 해당 범위의 모든 하위 Work Order를 뜻하며 작업지시서가 이를 더 좁혀야 한다.

| 계약 영역 | 설계 조항 | 결정 | Work Order | 여정 | 테스트 기준 | 증거/Gate |
| --- | --- | --- | --- | --- | --- | --- |
| 독립 제품·배포 | §1~4, §15, §22~24 | D001~D005, D012, D021 | M1-01~05, M3-01~06, M9-01~03 | WEB/WIN/AND/IOS 전체 | TP-1·TP-3·TP-5, `00`, `06` | CP1·CP2·RC |
| 적응형 3면 UI·화면 표준 | §5, §13 | D001, D016 | M2-01~08, M3-01~06 | 전체 | TP-1, `04`, `06` | G2-UX·CP2 |
| 5종 지식·권위·가중치 | §6~7 | D013·D014 | M2-03, M5-04, M6-04~07 | WEB-01, WIN-01~02, OPS-01 | TP-2·TP-3, `01` | CP3·CP4 |
| Vision/LLM-first·Parser/OCR 보완 | §8.1~8.3, INV-14~15 | D018 | M6-01~02, M6-08, M6-10~14, M7-01~04 | WEB-01, WIN-01, AND-01, IOS-01 | TP-2A·TP-2·TP-3, `03` | CP3·CP4 |
| RuleSet·충돌·검토 차단 | §7.2~7.4, §8.4 | D013 | M2-03~04, M6-06~07, M8-08~10 | WEB-01~02, WIN-02~03, OPS-01 | TP-2·TP-4, `01`, `04` | CP4·CP5 |
| 모델 선택·자동 Routing·Fallback | §9~10 | D006, D015, D019 | M2-04·07, M6-01~03, M6-11~14 | WEB-01, WIN-01~02, OPS-01 | TP-2A·TP-2·TP-3, `02` | CP3·CP4 |
| 인터넷·Daon Connector | §6.3~6.5, §11 | D007·D008 | M6-09, M6-15 | WEB-01, WIN-02, OPS-01 | TP-2·TP-3, `01`, `03`, `06` | CP4 |
| Local-private·Cloud-sync | §12, §15.2~15.3, §21.3 | D003·D005·D009 | M3-02~03, M5-01~07, M7-01~06 | WIN-01~03 | TP-2·TP-3·TP-5, `03`, `05`, `06` | CP4·RC |
| 생성 설정·5종 Studio | §13, §18.3 | D014·D016·D017 | M2-05, M8-01~13 | WEB-02, WIN-03, AND-01, IOS-01 | TP-1·TP-4, `04` | CP5 |
| 인증·권한·Step-up·현재 ACL | §14, §17~18 | D004·D017·D020 | M2-06, M4-01~07, M5-04 | 전체 | TP-2·TP-3·TP-5, `05` | CP4·RC |
| API·same-origin·Audit | §16~17, §20 | D004·D005 | M3-01, M4-01~07, M5-04 | WEB/WIN/OPS 전체 | TP-2A·TP-2·TP-3·TP-5, `05`, `06` | CP3·CP4·RC |
| Source/Run 상태·오디오·재처리 | §8.2, §18.1~18.2 | D018·D019 | M2-03~04·07, M6-08·10~14, M7-01~04 | WEB-01, WIN-01, AND-01, IOS-01, OPS-01 | TP-2A·TP-2·TP-3, `02`, `03`, `06` | CP3·CP4 |
| 보안·개인정보·Egress | §14, §19 | D004·D006~D009·D017·D020 | M4-02~06, M5-01~06, M6-02·09·11~15, M9-04~06 | 전체 | TP-2·TP-3·TP-5, `05`, `06` | CP4·RC |
| 운영·알림·백업·복구 | §20~21, §25 | D009~D012 | M2-07, M4-07, M5-07, M9-01~10 | OPS-01, 전체 회귀 | TP-1·TP-5, `06` | RC·G9 |
| Subagent·증거·승인 | §25~27 | D001~D020 | 모든 WO, M9-V01~V02 | 전체 | 모든 TP | Attempt Ledger·Evidence Manifest·Gates |

## Release 1 사용자 여정 책임

| 여정 | 주 구현 Work Order | 독립 검증 | 최종 증거 |
| --- | --- | --- | --- |
| R1-WEB-01 | M3-01, M4-01~05, M5-01~04, M6-01~15, M7-01~05 | TP-2A·TP-2·TP-3 | Browser 클릭·Network·DB/Object·RunSnapshot·Citation |
| R1-WEB-02 | M8-01~13 | TP-4 | 5종 실제 파일·GenerationSettingsSnapshot·Version/Approval/Audit |
| R1-WIN-01 | M3-02~03, M5-03~05, M6-03·08·10~14, M7 | TP-3 | EXE·패킷 캡처·Local Store·ASR/LLM·승인 Sync |
| R1-WIN-02 | M6-02~03·11~15, M7 | TP-2·TP-3 | Route·Model·Network·EgressDecision·Audit |
| R1-WIN-03 | M8-01~13 | TP-4 | 설치 App 클릭·5종 파일·검토/승인 계보 |
| R1-AND-01 | M3-04~05, M7-06, M8-11 | TP-3·TP-4 | APK·권한·Background·Offline·편집 Matrix |
| R1-IOS-01 | M3-04·06, M7-06, M8-11 | TP-3·TP-4 | Archive/설치 Build·Device/Simulator·편집 Matrix |
| R1-OPS-01 | M2-06~07, M4-07, M6-13~15, M9-01~10 | TP-1·TP-3·TP-5 | 운영 화면 클릭·경고·재처리·복구·Audit |

## 누락 점검

- 설계 §1~28: 위 계약 영역 중 하나 이상에 연결됨
- R1 여정 8종: 모두 구현·테스트·증거 연결됨
- 테스트 시나리오 `01`~`06`: 모두 하나 이상의 계약 영역에 연결됨
- Gate TP-0·TP-1·TP-2A·TP-2·TP-3·TP-4·TP-5 및 CP1~CP5·RC: 모두 연결됨

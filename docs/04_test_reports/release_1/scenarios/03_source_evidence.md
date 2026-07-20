# TS-SRC — Source 수명주기·처리·근거·Connector 테스트 시나리오

기준: 설계서 §8, §9, §16.2, §18.1, §19 · 불변 조건 INV-5, 7, 14, 15 · 버전 0.5 (2026-07-20)

> v0.5 정합: 설계 질의 N1(오디오 처리 sub-state)·N2(`waiting_model` 재처리)가 §8.2로 확정됨. 오디오 2경로(`audio_llm_understanding` / `speech_to_text→llm_semantic_understanding`)와 ASR-only Ready 금지, `waiting_model` 자동 Readiness Event·수동 재처리·`retry_of_processing_run_id`·Backoff·중복 억제를 반영.

## 1. 파일 등록·보안 검사 (TS-SRC-001~)

지원 형식: PDF, DOCX, PPTX, XLSX, CSV, TXT, Markdown, 주요 이미지 `[설계 Q5: 이미지 형식 목록]`, M4A, WAV, MP3

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SRC-001 | P2·L4 | 지원 형식 전수 등록 | 문서·표 7개 형식, M0에서 확정한 주요 이미지 각 형식, 음성 3개 형식의 정상 파일을 각 1개 이상 등록 | 전부 `registered→security_check→processing→indexing→ready` 도달, 원본 Digest 보존 | §8.2, §18.1 |
| TS-SRC-002 | P1·L4 | 확장자-실형식 위장 | 실행 파일을 `.pdf`로, ZIP을 `.docx`로 위장 등록 | MIME·실형식 검사에서 거부 또는 `failed`, 처리 파이프라인 진입 0건 | §8.2 |
| TS-SRC-003 | P1·L4 | 압축 폭탄 | 압축 폭탄 성격의 파일 등록 | 안전 거부, Worker 자원 고갈 없음 | §8.2 |
| TS-SRC-004 | P2·L4 | 암호화·손상 파일 | 암호로 보호된 PDF와 손상된 DOCX 등록 | `failed` 또는 `needs_review`로 명확한 사유 표시, 무한 재시도 없음 | §8.2, §18.1 |
| TS-SRC-005 | P2·L4 | 미지원 형식 거부 | EXE·미지원 형식 등록 시도 | 허용/거부 Matrix대로 거부, 안전 오류 표시 | §8.2 |
| TS-SRC-006 | P2·L3 | 크기 한도 | 한도 초과 파일 등록 `[M0: R1-D010 한도 확정]` | 한도 기준 거부, 명확한 안내 | §8.2 |
| TS-SRC-007 | P2·L3 | 직접 입력 SourceVersion | 텍스트 직접 입력 → 편집 → 재색인 | 편집마다 새 SourceVersion, 이전 버전 불변, 재색인 후 검색 반영 | §8.1, M6-05 |
| TS-SRC-008 | P2·L3 | 민감정보·Injection 검사 | 명령문·민감정보가 포함된 직접 입력 등록 | 검사 수행 기록, 명령문이 실행되지 않고 데이터로 취급 | M6-05, §20.3 |

## 2. 처리 상태 전이 (TS-SRC-010~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SRC-010 | P2·L3 | 정상 전이 경로 | 정상 파일 등록 후 상태·처리 단계 이벤트 추적 | `Vision/LLM 이해→Parser/OCR 검증→근거 조정→색인` 순서와 각 이벤트·시각 기록 | §8.2, §18.1 |
| TS-SRC-011 | P2·L4 | 처리 실패·재처리 | 처리 중 Worker 장애 주입 → 화면에서 재처리 | `failed` 표시와 사유, 재처리 후 `ready`, 사용자 CLI 개입 0건 | §18.1, §21.1 |
| TS-SRC-012 | P2·L4 | partial_understanding 검토 흐름 | 일부 페이지 Vision/LLM 이해 실패 문서 등록 후 사용자가 검토 요청 | 먼저 `partial_understanding`과 성공·누락 페이지를 표시하고 기본 검색·생성에서 제외, 검토 요청 후 `needs_review`로 전환 | §8.2, §18.1 |
| TS-SRC-013 | P2·L4 | expired·disabled 동작 | Source 권한 만료 유도, 별도 Source 비활성화 | 만료·비활성 Source는 새 검색에서 제외, 과거 Run 계보는 보존 | §18.1, §8.4 |
| TS-SRC-014 | P3·L3 | 미정의 전이 거부 | `deleted` 상태 Source의 재처리 등 비정상 전이를 API로 시도 | 거부, 안전 오류 | §18.1 |
| TS-SRC-015 | P1·L4 | Vision/LLM-first 의미 이해 | 문맥 의존 표현·표·이미지가 포함된 PDF 등록 | 의미 이해 모델·Digest·Prompt·Policy와 의미 청킹 결과가 ProcessingRun에 기록 | §3, §8.2, INV-14 |
| TS-SRC-016 | P1·L3 | Parser·OCR 검증·보완 전용 | Vision/LLM 해석과 Parser/OCR 추출 결과 및 처리 순서 검사 | Parser/OCR는 1차 의미 이해 뒤 문자·표·좌표·원문 위치 교차 검증·누락 보완만 수행. 물리적 병렬 실행 시 출력은 1차 이해 완료 전 격리되고 최초 의미 입력·대체·완료 판정 사용 0건 | §3, §8.2, INV-15 |
| TS-SRC-017A | P1·L4 | 정책 Hard Filter 후보 0 | 모든 Vision/LLM 후보를 데이터 영역·권한·Egress 정책으로 차단 | ProcessingRun `policy_blocked`·Source `needs_review`, 정책 원인 Code 표시, Parser-only `ready` 0건 | §8.2, §10.5, §18.1, INV-15 |
| TS-SRC-017B | P1·L4 | 정책 허용 모델 Runtime 전체 불가 | 정책상 허용된 Vision/LLM Deployment를 모두 Offline으로 만들고 재시도 소진 | ProcessingRun `failed/NO_AVAILABLE_UNDERSTANDING_MODEL`·Source `waiting_model`, 재시도 가능성과 사유 표시, Parser-only `ready` 0건 | §8.2, §10.5, §18.1, INV-15 |
| TS-SRC-017D | P1·L4 | `waiting_model` 자동 Readiness 재처리 | `auto`(또는 자동 허용 `local_only`) Source가 `waiting_model`인 상태에서 필요한 Deployment를 `ready/healthy`로 복귀 | Readiness Event가 재처리를 1회 자동 큐잉, `retry_of_processing_run_id`·`trigger_type=readiness`·현재 정책 Snapshot으로 새 ProcessingRun 생성, 성공 시 `indexing→ready` | §8.2 |
| TS-SRC-017E | P1·L4 | `waiting_model` 수동 재처리 | `pinned`/직접 선택 Source를 권한 사용자가 화면 또는 `POST /sources/{id}/processing-runs`로 재처리 요청 | 새 ProcessingRun 생성, 이전 실패 Run 불변, `trigger_type=manual` 기록 | §8.2 |
| TS-SRC-017F | P1·L3 | 재처리 중복 억제 | 짧은 간격에 자동 Readiness Event와 수동 요청을 동시 유발 | SourceVersion·역할별 활성 ProcessingRun 1개만 허용, Idempotency·Event 중복 제거·Backoff로 폭주 0건, 촉발 주체·이벤트 Audit 기록 | §8.2 |
| TS-SRC-017C | P1·L4 | 부분 의미 이해 | 다중 Page PDF에서 일부 Page Vision/LLM 이해만 성공하도록 장애 주입 | 성공·누락 범위 표시, Source `partial_understanding`, 기본 색인·검색·생성 사용 0건, 재처리→전체 성공 또는 검토·비활성화 전이 | §8.2, §18.1, INV-14·15 |
| TS-SRC-018 | P1·L4 | 의미 이해·추출 불일치 | Vision/LLM 해석과 Parser/OCR 추출이 의도적으로 다른 Fixture 처리 | 양쪽 결과·위치·버전 보존, 자동 은폐 없이 `needs_review` 전환 | §8.2, INV-15 |
| TS-SRC-019 | P1·L4 | Local-private 외부 Vision 차단 | Local-private 문서에 Local/Internal Vision 불가, External Vision 가용 상태 | External 자동 전송 0건, 안전 대기·실패 상태와 EgressDecision 기록 | §8.2, §10.5, INV-8·14 |

## 3. 원문 근거 (TS-SRC-020~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SRC-020 | P1·L4 | PDF Page 인용 재현 | 특정 페이지에만 있는 사실 질의 → 인용 클릭 | 근거 Viewer가 해당 Page·위치를 열고 인용 문구와 원문 일치 | §9.2, M6-09 |
| TS-SRC-021 | P1·L4 | XLSX Cell 인용 재현 | 특정 셀 값 질의 → 인용 확인 | Cell 단위 위치 재현 | M6-15 |
| TS-SRC-022 | P1·L4 | 이미지 Region 인용 | 이미지 내 특정 영역 정보 질의 | Region 좌표 기반 근거 표시 | M6-15 |
| TS-SRC-023 | P2·L4 | DOCX·PPTX·CSV·TXT·MD 근거 | 각 형식별 위치 인용 확인 | 형식별 정의된 단위(문단·슬라이드·행 등)로 원문 위치 재현 | M6-15 |
| TS-SRC-024 | P2·L4 | 표 구조 이해 | 병합 셀·다단 헤더가 있는 표 질의 | 표 구조가 보존된 근거, 잘못된 셀 매핑 없음 | M6-15 |
| TS-SRC-025 | P2·L4 | 근거 Drawer 문맥 보존 | 대화 중 근거 열기 → 닫기 | 작업 문맥(대화 위치·편집 위치) 유지 | §5.2 |
| TS-SRC-026 | P2·L3 | Source 버전과 인용 고정 | 인용된 Source를 새 버전으로 교체 → 과거 답변의 인용 클릭 | 과거 인용은 당시 SourceVersion의 원문을 표시 | §8.1, §16.1 |

## 4. 음성·ASR (TS-SRC-030~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SRC-030 | P2·L4 | 음성 3형식 전사 | M4A·WAV·MP3 각 1건 등록 | 원본 보존 + TranscriptVersion 생성, 시간 구간(Segment) 포함 | §8.2 |
| TS-SRC-030A | P1·L4 | 오디오 의미 이해 2경로 | (a) Audio-capable LLM 직접 이해 허용 / (b) `speech_to_text→text LLM` 경로 각각 처리 | (a)는 `audio_llm_understanding`, (b)는 `speech_to_text→llm_semantic_understanding→transcript_timecode_validation` sub-state를 거쳐 의미 이해·시간 구간 검증·Evidence reconciliation·색인 완료 후 `ready` | §8.2, §18.1, INV-14 |
| TS-SRC-030B | P1·L4 | ASR-only Ready 금지 | 전사만 성공하고 의미 이해·시간 구간 검증이 미완/미달인 오디오 처리 | `ready` 전환 0건, `transcript_review`로 위장 0건, `needs_review` 분기 | §8.2, §18.1 |
| TS-SRC-031 | P1·L4 | 시간 구간 인용 재현 | 전사문 특정 구간 근거 질의 → 인용 클릭 | 해당 시간 구간 재생 위치로 이동 | §8.2, M7 Exit |
| TS-SRC-032 | P1·L4 | Local ASR 오프라인 | Windows 네트워크 차단 상태에서 음성 등록·전사 | Local ASR로 전사 완료, 외부 호출 0건 | §8.2, R1-WIN-01 |
| TS-SRC-033 | P1·L3 | ASR 계보 | TranscriptionRun 기록 검사 | Provider Profile·Deployment·Model Digest·Routing Policy·언어·시간 구간·검토 Version 전부 기록 | §8.2 |
| TS-SRC-034 | P1·L4 | Local-private 음성의 External ASR 배제 | Local-private 영역 음성 파일 처리, External ASR 후보 존재 상태 | 명시 승인 없이 External 후보 포함 0건 | §8.2, INV-8 |
| TS-SRC-035 | P2·L3 | 전사 검토 버전 | 전사문 오류 수정(검토) 수행 | 검토 결과가 별도 Version, 원 전사·원본 불변 | §8.2 |

## 5. 검색·Retrieval·결과 상태 (TS-SRC-040~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SRC-040 | P2·L3 | Hybrid 검색 동작 | 키워드성 질의와 의미성 질의 각각 실행 | 두 유형 모두 관련 Source 검색, 점수·rerank 순위가 운영 메타데이터로 기록 | §9.2 |
| TS-SRC-041 | P2·L3 | 검색 전후 ACL 이중 검증 | 권한 없는 Source가 인덱스에 존재하는 상태에서 질의 | 검색 전 Filter와 후 검증 모두 동작, 무권한 Source 인용 0건 | §9.2 |
| TS-SRC-042 | P2·L3 | Vector Recall 주기 검증 | Approximate 검색과 정확 검색 비교 절차 실행 | Recall 기준 충족 `[M0: 기준값 확정]` | §9.2 |
| TS-SRC-043 | P2·L4 | 결과 상태 6종 표시 | 근거 충분·부분·부족·충돌·RuleSet 검토·만료를 각각 유도 | 각 상태가 정확히 표시되고 사실 확정 회피 규칙 적용 | §9.3 |
| TS-SRC-044 | P2·L4 | ready 아닌 Source 제외 | 처리 중·실패 Source가 섞인 Workspace에서 질의 | ready Source만 사용, 누락 범위 표시 | §21.2 |

## 6. 인터넷 Connector (TS-SRC-050~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SRC-050 | P2·L4 | 검색→Snapshot 등록 | 인터넷 검색 실행 후 결과를 지식으로 사용 | Query·Provider 기록, 서버측 Safe Fetch로 Snapshot 생성, 브라우저 직접 fetch 0건 | §8.3 |
| TS-SRC-051 | P2·L3 | 출처 메타데이터 보존 | Snapshot의 저장 항목 검사 | URL, 제목, 게시·조회 시각, 저작권·접근 상태 보존 | §8.3, §19.3 |
| TS-SRC-052 | P2·L3 | 변경 페이지 새 버전 | 동일 URL을 내용 변경 후 재수집 | 새 SourceVersion 생성, 과거 Run은 과거 Snapshot 참조 | §8.3 |
| TS-SRC-053 | P1·L4 | Allowlist·Blocklist | 조직 Blocklist URL 수집 시도 | 거부, 정책 사유 표시 (SSRF 공격 시나리오는 TS-SEC-060대) | §19.3 |
| TS-SRC-054 | P2·L4 | 인터넷 권한 없는 사용자 | 인터넷 검색 권한이 없는 역할로 검색 시도 | 거부, 잠금 이유 표시 | §14.2 |

## 7. Daon 지식 Connector (TS-SRC-060~)

`[M0: R1-D007 Daon Sandbox 확보 전에는 계약 Mock으로 선행하되 최종 판정은 공식 Sandbox]`

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SRC-060 | P2·L4 | 승인 지식 검색·사용 | Daon 연결 후 승인 지식 포함 질의 | 외부 ID·승인 상태·버전·유효기간 저장, 실행에 사용 버전 Snapshot 고정 | §8.4 |
| TS-SRC-061 | P1·L4 | 실행 전 발행 상태 확인 | Daon 측에서 지식 회수(발행 취소) 후 질의 | 실행 전 확인에서 감지, 회수된 지식 사용 0건 | §8.4 |
| TS-SRC-062 | P1·L4 | 접근 만료 후 차단·계보 보존 | Daon 권한 만료 유도 → 새 질의 + 과거 Run 조회 | 새 실행 차단, 과거 감사 계보는 열람 가능 | §8.4 |
| TS-SRC-063 | P2·L3 | Connector 오류 계약 | Timeout·오류 응답·비호환 버전 주입 | Timeout·Retry·Circuit Breaker 동작, 안전 오류 Mapping, 원문 오류 비노출 | §19.1, §19.2 |
| TS-SRC-064 | P2·L3 | RuleSet 본문 은닉 | Client API 응답 검사 | RuleSet 본문·Daon 내부 식별자 없음, Reference·Version·상태·평가 결과만 반환 | §17.2 |

## 8. 삭제·보존 (TS-SRC-070~)

전용 Fixture만 사용. 기존 데이터 삭제 금지.

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-SRC-070 | P2·L4 | 유예 삭제 | Source 삭제 실행 | 비활성화→유예→파생 정리 순서, 즉시 물리 삭제 0건 | §16.2 |
| TS-SRC-071 | P2·L3 | 파생 데이터 추적 정리 | 삭제 완료 후 Index·Preview·Cache·Local Copy 잔존 검사 | 추적 정리 완료, 삭제 상태 화면 확인 가능 | §16.2, §21.4 |
| TS-SRC-072 | P1·L3 | Legal Hold 우선 | Hold 걸린 Source 삭제 시도 | 삭제 차단, Hold 우선 적용 | §16.2 |
| TS-SRC-073 | P2·L3 | 감사 최소 계보 분리 보존 | 삭제 완료된 Source를 인용한 과거 Run 조회 | 콘텐츠는 삭제, 감사에 필요한 최소 계보는 정책대로 보존 | §16.2 |

## 설계 확인 필요 사항

- `[설계 Q5]` "주요 이미지" 형식의 정확한 목록 (TS-SRC-001) — M0(R1-D) 확정 예정
- `[해소]` N1(오디오 처리 sub-state) — §8.2 오디오 2경로(`audio_llm_understanding` / `speech_to_text→llm_semantic_understanding→transcript_timecode_validation`)와 ASR-only Ready 금지로 확정 (TS-SRC-030A·030B)
- `[해소]` N2(`waiting_model` 재처리) — §8.2 자동 Readiness Event·수동 재처리·`retry_of_processing_run_id`·중복 억제로 확정 (TS-SRC-017D~F)

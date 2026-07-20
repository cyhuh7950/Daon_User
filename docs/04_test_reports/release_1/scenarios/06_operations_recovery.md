# TS-OPS — 운영·축소 운영·오프라인·복구·플랫폼 테스트 시나리오

기준: 설계서 §5.3, §21, §22, §23.1 · 불변 조건 INV-1, 13 · 버전 0.5 (2026-07-20)

> v0.5 정합: 개정 §21.1 운영 화면에 추가된 `waiting_model` 재처리 Queue·Backoff·중복 억제, Step-up 실패·만료, 과거 결과 AccessDecision 차단·마스킹 현황을 운영 대시보드 검증에 반영. §21.4 복구 목표·훈련 결과 관리와 §23.1 CP1~CP5·RC(CP3은 테스트 계획 §1.4 TP-2A)도 유지.

> 파괴적 복구·운영 데이터 대상 Restore는 G9-DRILL 승인 없이 수행하지 않는다. 아래는 전용 Fixture·격리 환경 기준이다.

## 1. 운영 화면 (TS-OPS-001~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-OPS-001 | P2·L4 | 운영 상태 대시보드 | 운영 화면 조회 `[M2]` | API·Worker·DB·Object·Model·Node·Connector·비용·Backup 상태 표시 | §21.1 |
| TS-OPS-002 | P2·L4 | 처리·실패 Queue 가시성 | Source 처리 실패 유도 후 운영 화면 확인 | 실패 Queue와 재처리 경로 표시 | §21.1 |
| TS-OPS-002A | P2·L4 | waiting_model·재처리 Queue 가시성 | `waiting_model` Source와 자동·수동 재처리 Queue 유발 후 운영 화면 확인 | `waiting_model` 수·필요 역할·Readiness Event·자동/수동 재처리 Queue·Backoff·중복 억제 상태 표시 | §21.1 |
| TS-OPS-002B | P2·L4 | 보안 운영 가시성 | Step-up 실패·만료와 과거 결과 AccessDecision 차단 유발 | Step-up 추가 인증 실패·만료, 과거 결과 AccessDecision 차단·마스킹 현황 표시 | §21.1 |
| TS-OPS-003 | P2·L4 | 정책 잠금 반영 | 운영자가 정책·권한·Provider·RuleSet·가중치 잠금 설정 → 사용자 화면 교차 확인 | 잠금이 사용자 화면에 실제 반영, Policy Version·Audit 기록 | §23.1 R1-OPS-01 |
| TS-OPS-004 | P2·L4 | 화면만으로 운영 완주 | 주요 운영 작업(상태 확인·재처리·정책 변경·복구 트리거)을 화면만으로 수행 | Python·DB·CLI 개입 0건 | §2.1, INV-13 |

## 2. 축소 운영 (TS-OPS-010~)

각 장애는 전용 환경에 주입한다.

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-OPS-010 | P1·L4 | Daon 장애 | Daon Connector 장애 주입 | Daon 지식·엔진만 비활성, 유효 Snapshot 강제 RuleSet은 계속 적용, Binding 없는 Workspace 독립 기능 정상 | §21.2, INV-1 |
| TS-OPS-011 | P1·L4 | 외부 LLM 장애 | External Provider 장애 주입, `auto`와 `pinned` 각각 실행 | `auto`는 허용된 Local·Internal·다른 External 자동 시도, `pinned`은 무단 변경 없이 대안 제안 | §21.2 |
| TS-OPS-012 | P1·L4 | Local LLM 장애 | Local 모델 장애 주입 | 무단 외부 전환 0건 | §21.2, INV-10 |
| TS-OPS-013 | P2·L4 | 인터넷 장애 | 인터넷 Connector 차단 | 보유 지식 실행 여부 안내, 인터넷 의존 기능만 축소 | §21.2 |
| TS-OPS-014 | P1·L4 | Index 장애 | Index 서비스 장애 주입 | Ready Source만 사용, 누락 범위 표시 | §21.2 |
| TS-OPS-015 | P1·L4 | Evidence Store 장애 | Evidence Store 차단 후 승인·전달 시도 | 승인·전달 차단 (근거 무결성 보호) | §21.2 |
| TS-OPS-016 | P2·L4 | 장애→경고→재처리→복구 흐름 | 위 장애 중 하나를 주입 후 운영 화면에서 복구까지 수행 | 경고 표시→재처리→복구 완료가 화면·Audit에 일치 | §21.1, M9-02 |

## 3. 오프라인·동기화 (TS-OPS-020~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-OPS-020 | P1·L4 | Windows Local-private 오프라인 | 네트워크 차단 상태에서 로컬 Source 검색·로컬 LLM 질의·근거 조회·Studio 초안 생성·편집 | 전부 동작, 암호화 Cache·RunSnapshot·작업 Queue 보존, 외부 연결 0건 | §21.3, R1-WIN-01 |
| TS-OPS-021 | P1·L4 | 오프라인 제한 기능 | 오프라인 상태에서 인터넷·Daon·External·최종 승인·외부 전달 시도 | 사용 불가 안내, 무단 실행 0건 | §21.3 |
| TS-OPS-022 | P1·L4 | 재연결 승인 Sync | 연결 복구 후 동기화 | 승인된 항목만 Sync, Version 비교 후 충돌 자동 덮어쓰기 0건 | §21.3 |
| TS-OPS-023 | P2·L4 | 충돌 처리 | 오프라인 편집과 서버 편집이 충돌하는 상태에서 재연결 | 충돌 표시, 사용자 선택 요구, 자동 병합·덮어쓰기 0건 | §21.3 |
| TS-OPS-024 | P2·L4 | 모바일 제한 열람 | 모바일에서 다운로드한 자료·결과를 오프라인 열람 | 제한 열람 동작, 무권한 기능 차단 | §4.2, §21.3 |
| TS-OPS-025 | P1·L4 | 장치 Revoke 시 로컬 Key 폐기 | 오프라인 장치를 Revoke → 재연결 | Local Sync Key 폐기, 접근 불가 | §21.3 |

## 4. Backup·Restore·복구 (TS-OPS-030~)

전용 Fixture·격리 환경만. 운영 대상은 G9-DRILL 승인 필수.

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-OPS-030 | P2·L4 | Cloud Backup→격리 Restore | 격리 환경에서 Backup 생성 후 Restore | 복구 후 권한·계보 재검증 통과 | §21.4, M5-07 |
| TS-OPS-031 | P2·L4 | Local 손상 복구 | 로컬 저장소 손상 유도 후 복구 | 손상 복구 동작, 데이터 무결성 확인 | §21.4, M5-07 |
| TS-OPS-032 | P2·L4 | RTO/RPO 훈련 | 격리 환경에서 재해 복구 훈련 `[M0: R1-D009 RTO/RPO 확정]` | 복구 목표 충족, 결과를 운영 화면에서 관리 | M9-07 |
| TS-OPS-033 | P2·L3 | 복구 후 감사 무결성 | Restore 후 감사 기록 검사 | 감사 위변조 방지 유지, 계보 연속성 | §21.4 |

## 5. 배포·Update·Rollback (TS-OPS-040~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-OPS-040 | P2·L4 | Web·Cloud 배포·Rollback | Production-like 내부 배포 후 Rollback | 배포·종료·재기동·Rollback 동작 (외부 환경은 G9-DEPLOY 선행) | M9-03 |
| TS-OPS-041 | P2·L4 | Windows 서명 설치·Update·Rollback | 서명 Installer 설치 → Update → Rollback | 서명 확인, 설치·종료·재기동·Update·Rollback 실증, Local Service 포함 | M9-04 |
| TS-OPS-042 | P2·L4 | Android 서명 Build·Update | 서명 APK/AAB 설치·Update | 실기기 설치·권한·Update·Rollback/복구 | M9-05 |
| TS-OPS-043 | P2·L4 | iOS 서명 Build·Update | Archive/설치 Build `[환경: macOS 빌드 호스트 필요 — 미확보 시 BLOCKED]` | Device/Simulator 설치·서명·권한·Update·복구 | M9-06 |
| TS-OPS-044 | P1·L4 | 종료 후 잔존 자원 0 | 각 플랫폼 종료 후 Process·Port·Local Node 검사 | 잔존 0건 | M3 Exit, §21.4 |

## 6. 반응형·상태 보존·접근성 (TS-OPS-050~)

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-OPS-050 | P1·L4 | 4개 폭 구간 전환 | 1440+·1024~1439·600~1023·599- 구간을 순차 전환 | 각 구간의 3면/Drawer/탭 동작이 §5.3 정의와 일치 | §5.3 |
| TS-OPS-051 | P1·L4 | 전환 시 5개 상태 보존 | 작업 중(Source 선택·대화 위치·실행 진행·편집 위치·근거 뷰어 위치) 폭 전환 | 5개 상태 전부 보존 | §5.3 |
| TS-OPS-052 | P2·L4 | 키보드 접근성 | 주요 흐름을 키보드만으로 수행 | 조작 가능, 포커스 순서 논리적 | M9-10 |
| TS-OPS-053 | P2·L4 | Screen Reader | 주요 화면 Screen Reader 탐색 | 의미 있는 레이블·상태 안내 | M9-10 |
| TS-OPS-054 | P2·L3 | 지원 OS/Browser Matrix | M0 확정 지원 대상 `[M0: R1-D001]` 전수 확인 | Matrix 전 항목 동작 | M9-10 |

## 7. 성능·용량·비용 (TS-OPS-060~)

`[M0: R1-D010 SLO·한도 확정 후 합격 기준값 채움]`

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-OPS-060 | P2·L4 | 파일·Index 처리 성능 | 규정 규모 파일 처리 시간 측정 | SLO 충족 | M9-09 |
| TS-OPS-061 | P2·L4 | 동시 사용자·Run | 동시 부하 인가 | 응답시간·처리량 SLO 충족, 축소 운영 보고 | M9-09 |
| TS-OPS-062 | P2·L4 | Local HW 성능 | Local LLM 실행 성능 측정 | 진단 기준 대비 동작, 병목 보고 | M9-09 |
| TS-OPS-063 | P3·L3 | 비용 한도 | Provider 비용 한도 도달 유도 | 한도 기준 처리, 비용 가시성 | M9-09 |

## 8. 플랫폼 여정 통합 (TS-OPS-070~)

R1 필수 여정 8종은 테스트 계획 §7에서 직접 재실행한다. 아래는 운영 관점 통합 확인이다.

| ID | P·수준 | 시나리오 | 절차 | 기대 결과 | 조항 |
| --- | --- | --- | --- | --- | --- |
| TS-OPS-070 | P1·L4 | Local-private·Cloud-sync 병존 | 한 사용자가 두 영역 Workspace를 오가며 작업 | 영역 분리 유지, 데이터 교차 0건 | §6.2 |
| TS-OPS-071 | P1·L4 | 크로스 플랫폼 정본 일치 | Cloud-sync 산출물을 Web·Windows·모바일에서 조회 | 동일 정본, 버전 일치 | §6.2 |
| TS-OPS-072 | P2·L4 | 여정 간 회귀 | 전체 여정 수행 후 기존 기능 재확인 | 신규 검증이 기존 기능을 깨지 않음 | 테스트 계획 §8 |

## 환경 확인 필요 사항

- `[환경]` iOS(TS-OPS-043): macOS 빌드 호스트 미확보 시 검증 불가 — M0 확보 여부 확정 필요
- `[M0]` 지원 OS/Browser(R1-D001), RTO/RPO(R1-D009), SLO·한도(R1-D010) 확정 후 합격 기준 확정

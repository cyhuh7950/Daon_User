# APR-R1-M5-07-WINDOWS-NATIVE-LOGIN-20260810-01

## 승인 정보

| 항목 | 내용 |
| --- | --- |
| 승인 ID | `APR-R1-M5-07-WINDOWS-NATIVE-LOGIN-20260810-01` |
| 승인자 | 신산님 |
| 승인일 | 2026-08-10 |
| 상태 | `APPROVED` |
| 결정 ID | `R1-D028` |
| 적용 Work Order | `R1-M4-03`, `R1-M5-07` |

## 승인 범위

- 기존 Web 로컬 로그인 `POST /api/v1/auth/login`과 Secure·HttpOnly Cookie 동작은 변경하지 않는다.
- Windows 설치형 로컬 계정용 `POST /api/v1/auth/native/login`을 별도 공개 API로 추가한다.
- 요청은 `login_id`, `password`만 허용하고 서버가 Platform `windows`, Client Kind `native`를 고정한다.
- 성공 응답은 Native Rust 계층에 opaque Access·Refresh Credential과 Safe Session Projection을 전달하며 Cookie는 발급하지 않는다.
- Refresh 회전·재사용 탐지·Session 철회는 기존 Native Identity 계약을 재사용한다.
- Windows Credential은 Rust가 전용 Windows Credential Manager Target에 저장하고 WebView JavaScript·로그·환경 변수·Evidence에 노출하지 않는다.
- Windows Recovery Tauri Bridge는 이 Native Session으로 Cloud Recovery 공개 API 7종을 호출하고, Local Recovery는 별도 App Instance Credential로 Loopback 3종만 호출한다.

## 제외·안전 경계

- Web 로그인 응답에 Native Token 추가 금지
- Client가 `platform`·`client_kind`를 지정하거나 덮어쓰기 금지
- WebView의 직접 Gateway·Loopback 호출과 CSP 완화 금지
- Local Storage Root Key와 Native Session Credential 공유 금지
- 운영 데이터 Restore·제자리 덮어쓰기·파괴적 손상 주입 금지
- G9-DRILL 없는 운영 Restore 금지

## 승인 근거

신산님은 2026-08-10 어울1의 별도 Windows Native 로그인 API 권고안을 명시적으로 승인했다. 이 기록은 공개 API·보안 경계 변경의 사전 승인 증거이며 구현 완료 증거가 아니다.

# 로그인 복구 후 Recovery API 읽기 관찰

- 관찰 시각: `2026-08-10T10:42:42+09:00`
- 대상 URL: `https://daon-user.sinsan.kr/operations`
- Runtime 제품 Commit: `061bc4dcbddfd839fdcb64aa21ed498fe1e70e0b`
- 로컬 정본 HEAD: `0f9b2bc8cd6fd6ccee290ede5530bc9466bff66d` (Runtime Build SHA가 아님)

## 읽기 전용 흐름

1. 신산님이 명시한 Chrome 로그인 완료 후 새 검증 Tab으로 대상 URL을 열었다.
2. `Backup·Restore 실제 API` 상태가 처음에는 `working`으로 표시됐다.
3. 1초 후 화면 상태가 `failed · RESOURCE_UNAVAILABLE · Trace trace-bff-a0e83d68-af6b-4db6-9157-8f55a72c5c30`으로 전이했다.
4. 허용된 읽기 전용 버튼 `목록 새로고침`을 한 번만 눌렀다. 이는 Backup 생성·Preview·Execute·Cancel을 호출하지 않는다.
5. 새로고침 후에도 `failed · RESOURCE_UNAVAILABLE · Trace trace-bff-7be791bd-e230-4088-8587-f3e435de93e7`이 표시됐다.

## Network 증거 한계

- Browser Client의 공개 표면에는 Network event/response API가 없고, 제공된 `dev` 표면은 Console log만 제공한다.
- Console log는 0 entries였다.
- DOM evaluator와 Element evaluator에서 Browser `performance` Resource Timing이 노출되지 않았다.
- 따라서 실제 session 및 Backup 목록 GET의 URL·method·HTTP status는 이 승인된 Browser 제어 표면에서 얻지 못했다. same-origin 및 Browser 내부주소/localhost 직접 호출 0건도 `NOT_PROVEN`이다.

## 안전 경계와 판정

- 자격증명 입력, Cookie·Local Storage·Session 저장소 열람, Backup 생성, Restore Preview·Execute·Cancel, SSH·DB·Docker·직접 API 호출은 0건이다.
- 화면이 API/Adapter 성공이 아니라 `RESOURCE_UNAVAILABLE`을 표시하므로 `WEB_EVIDENCE_PASS`로 주장하지 않는다.
- 새 검증 Tab은 수집 직후 `chrome.tabs.finalize({})`로 종료했고 Browser 제어를 신산님에게 반환했다.

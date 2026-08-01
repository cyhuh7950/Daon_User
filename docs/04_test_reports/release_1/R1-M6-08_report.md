# R1-M6-08 결과보고서

## 판정

`COMPLETED` (CP3 Core Index·Retrieval 내부 계약 범위).

## 판단 이유

- PDF Chunk에 Source ID·Version·Page·Text·Chunk ID를 보존한다.
- 검색은 지정 SourceVersion으로 고정되어 다른 버전과 섞이지 않는다.
- 질문 토큰 중첩 점수로 관련 Chunk를 정렬하고 근거 Page를 반환한다.
- 전용 3개 및 API 전체 178개 테스트(25 skipped)가 통과했다.

## 조치

- 구현·테스트·진행·결과 문서를 커밋하고 원격 branch에 push한다.
- 외부 Vector DB·실제 Object Storage·Web E2E는 R1-M6-10에서 별도 검증한다.

## 실행 증거

```text
$env:PYTHONPATH='src'; uv run python -m unittest tests.test_pdf_index
Ran 3 tests ... OK

$env:PYTHONPATH='src'; uv run python -m unittest discover -s tests -p 'test_*.py'
Ran 178 tests in 9.849s
OK (skipped=25)
```

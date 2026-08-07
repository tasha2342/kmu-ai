# 골드셋 재검증 (kmu-ai 추출본 기준)

rag-test의 48문항을 계명대 규정집 181문서 코퍼스에 그대로 쓸 수 있는지 확인한 결과입니다.
**정답은 바꾸지 않았습니다.** 어디까지 확인되는지만 기록합니다.

- 표 추출(hwp5html) 포함 여부: **아니오**
- 문항 수: 48

| 판정 | 수 | 의미 |
| --- | --- | --- |
| `body` | 37 | 본문에서 정답 확인 |
| `tables` | 0 | 표에서 정답 확인 |
| `needs_tables` | 10 | 본문에 없음. 표 추출이 있어야 함 |
| `abstain` | 1 | 기권이 정답 |
| `missing` | 0 | 확인 실패 — 조사 필요 |

## 본문에서 확인되지 않은 문항

| ID | 유형 | 하위유형 | 문서 | 정답 | 판정 |
| --- | --- | --- | --- | --- | --- |
| T003 | table | cell_value | 1-0-1 | `267` | `needs_tables` |
| T004 | table | cell_value | 1-0-1 | `436` | `needs_tables` |
| T006 | table | cell_value | 1-0-1 | `1700` | `needs_tables` |
| T010 | table | cell_value | 1-0-4 | `특 2 호봉` | `needs_tables` |
| T011 | table | cell_value | 1-0-4 | `수당지급 월 봉급의 80%` | `needs_tables` |
| T012 | table | cell_value | 1-0-4 | `수당지급 월 봉급의 90%` | `needs_tables` |
| T013 | table | cell_value | 1-0-4 | `수당지급 월 봉급의 80%` | `needs_tables` |
| T015 | table | cell_value | 1-0-4 | `봉급의 2할 감액지급` | `needs_tables` |
| T016 | table | cell_value | 1-0-6 | `퇴직당시 월 기본급 72% × 정년잔여월수` | `needs_tables` |
| T017 | table | cell_value | 1-0-6 | `퇴직당시 월 기본급 48% × 정년잔여월수(최대 120개월)` | `needs_tables` |

## 해석

`needs_tables` 는 골드셋의 결함이 아닙니다. `hwp5txt`가 표를 `<표>` 자리표시자로만 남기기
때문에 생기는 것이고, 리포트 6.2절이 지목한 바로 그 문제입니다. 표 추출 경로가 돌면 채워집니다.

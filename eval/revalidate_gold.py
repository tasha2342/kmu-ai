"""골드 48문항을 kmu-ai 추출본 기준으로 재검증합니다.

골드셋은 rag-test에서 만들어졌고 그쪽 `eval/corpus_text/*.txt` 기준으로 검수됐습니다
(2026-07-21, 48/48 확정). 그런데 kmu-ai의 추출본은 그것과 바이트 동일하지 않습니다.
rag-test 쪽에는 `-----CHUNK-----` 구분자와 중복된 문단이 섞여 있고 kmu-ai 쪽이 더 깨끗합니다.

그래서 "이 골드셋을 kmu-ai 코퍼스에 그대로 쓸 수 있는가"를 먼저 확인해야 합니다.
정답을 바꾸지는 않습니다. 어디까지 찾을 수 있는지 기록만 합니다.

판정:
    `body`         본문에서 정답을 찾음 → 그대로 사용 가능
    `needs_tables` 본문에는 없음. 표 추출(hwp5html)이 있어야 채워짐
    `abstain`      기권이 정답인 문항 (T024)
    `missing`      표까지 있어도 못 찾음 → 조사 필요
"""

from __future__ import annotations

import argparse
import re
import unicodedata

from pathlib import Path
from typing import Any, Optional

from eval.corpus import Document, load_documents
from eval.run_eval import load_questions


OUT_PATH = Path(__file__).resolve().parent / "gold_delta.md"


def _loose_date_present(iso: str, text: str) -> bool:
    """`2026-07-01` 이 `2026. 7. 1.` `2026년 7월 1일` 형태로 본문에 있는지."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", iso or "")
    if not m:
        return False
    year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
    pattern = rf"{year}\s*[.\-년]\s*0?{month}\s*[.\-월]\s*0?{day}"
    return bool(re.search(pattern, text))


def _present(question: dict[str, Any], text: str) -> bool:
    candidates = [question.get("answer")] + list(question.get("acceptable_answers") or [])
    normalized = unicodedata.normalize("NFC", text)
    squashed = normalized.replace(" ", "")
    for candidate in candidates:
        if not candidate:
            continue
        value = unicodedata.normalize("NFC", str(candidate))
        if value in normalized or value.replace(" ", "") in squashed:
            return True
        if _loose_date_present(value, normalized):
            return True
    return False


def revalidate(with_tables: bool = False) -> list[dict[str, Any]]:
    docs: dict[str, Document] = {}
    for doc in load_documents(with_tables=with_tables):
        if doc.doc_id:
            docs[doc.doc_id] = doc

    rows: list[dict[str, Any]] = []
    for question in load_questions():
        doc = docs.get(question["doc_id"])
        if doc is None:
            rows.append({**question, "status": "missing", "note": "문서를 코퍼스에서 찾을 수 없음"})
            continue

        if question.get("normalize") == "unknown":
            rows.append({**question, "status": "abstain", "note": "기권이 정답인 문항"})
            continue

        if _present(question, doc.text):
            rows.append({**question, "status": "body", "note": ""})
            continue

        table_text = "\n".join(
            "\n".join("\t".join(row) for row in (t.get("rows") or [])) for t in doc.tables
        )
        if table_text and _present(question, table_text):
            rows.append({**question, "status": "tables", "note": "표에서 확인됨"})
            continue

        rows.append(
            {
                **question,
                "status": "needs_tables" if not with_tables else "missing",
                "note": "본문에 없음 (표 추출 필요)" if not with_tables else "표까지 봐도 못 찾음",
            }
        )
    return rows


def write_report(rows: list[dict[str, Any]], with_tables: bool) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    lines = [
        "# 골드셋 재검증 (kmu-ai 추출본 기준)",
        "",
        "rag-test의 48문항을 계명대 규정집 181문서 코퍼스에 그대로 쓸 수 있는지 확인한 결과입니다.",
        "**정답은 바꾸지 않았습니다.** 어디까지 확인되는지만 기록합니다.",
        "",
        f"- 표 추출(hwp5html) 포함 여부: **{'예' if with_tables else '아니오'}**",
        f"- 문항 수: {len(rows)}",
        "",
        "| 판정 | 수 | 의미 |",
        "| --- | --- | --- |",
        f"| `body` | {counts.get('body', 0)} | 본문에서 정답 확인 |",
        f"| `tables` | {counts.get('tables', 0)} | 표에서 정답 확인 |",
        f"| `needs_tables` | {counts.get('needs_tables', 0)} | 본문에 없음. 표 추출이 있어야 함 |",
        f"| `abstain` | {counts.get('abstain', 0)} | 기권이 정답 |",
        f"| `missing` | {counts.get('missing', 0)} | 확인 실패 — 조사 필요 |",
        "",
        "## 본문에서 확인되지 않은 문항",
        "",
        "| ID | 유형 | 하위유형 | 문서 | 정답 | 판정 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if row["status"] in ("body", "abstain"):
            continue
        lines.append(
            f"| {row['id']} | {row['type']} | {row.get('subtype')} | {row['doc_id']} "
            f"| `{row.get('answer')}` | `{row['status']}` |"
        )

    lines += [
        "",
        "## 해석",
        "",
        "`needs_tables` 는 골드셋의 결함이 아닙니다. `hwp5txt`가 표를 `<표>` 자리표시자로만 남기기",
        "때문에 생기는 것이고, 리포트 6.2절이 지목한 바로 그 문제입니다. 표 추출 경로가 돌면 채워집니다.",
        "",
    ]
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:24]))
    print(f"\n[재검증] {OUT_PATH} 저장")


def main() -> None:
    parser = argparse.ArgumentParser(description="골드셋 재검증")
    parser.add_argument("--with-tables", action="store_true", help="hwp5html 표까지 확인 (pyhwp 필요)")
    args = parser.parse_args()
    rows = revalidate(with_tables=args.with_tables)
    write_report(rows, with_tables=args.with_tables)


if __name__ == "__main__":
    main()

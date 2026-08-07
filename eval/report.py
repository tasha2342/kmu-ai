"""실험 결과를 한 표로 모읍니다.

`eval/results/*_summary.json` 을 읽어 rag-test `comparison.json` 과 같은 형식으로 정리합니다.
"""

from __future__ import annotations

import argparse
import json

from pathlib import Path
from typing import Any


RESULTS_DIR = Path(__file__).resolve().parent / "results"

# rag-test 리포트 5.1/5.2절 수치. 비교 기준선으로 표 맨 위에 깔아 둡니다.
RAGTEST_BASELINE = [
    {
        "experiment": "rag-test E4_hybrid_k12",
        "documents": 8,
        "chunks": 654,
        "answer_accuracy": 0.9167,
        "date_accuracy": 0.9583,
        "table_accuracy": 0.875,
        "recall_at_k": 0.8958,
        "mrr": 0.762,
        "note": "e5-small / extractive(정답 열람 편향 있음)",
    },
    {
        "experiment": "rag-test E4_gen_full",
        "documents": 8,
        "chunks": 654,
        "answer_accuracy": 0.7917,
        "date_accuracy": 0.875,
        "table_accuracy": 0.7083,
        "recall_at_k": 0.8958,
        "mrr": 0.762,
        "note": "e5-small / Gemma 생성",
    },
]

COLUMNS = [
    ("experiment", "실험", 26),
    ("documents", "문서", 5),
    ("chunks", "청크", 6),
    ("top_k", "k", 4),
    ("answer_accuracy", "정확도", 7),
    ("date_accuracy", "날짜", 7),
    ("table_accuracy", "표", 7),
    ("recall_at_k", "R@k", 7),
    ("mrr", "MRR", 7),
]


def load_summaries(names: list[str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(RESULTS_DIR.glob("*_summary.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if names and data.get("experiment") not in names:
            continue
        rows.append(data)
    return rows


def _fmt(value: Any, width: int) -> str:
    if value is None:
        return "-".rjust(width)
    if isinstance(value, float):
        return f"{value:.3f}".rjust(width)
    return str(value).rjust(width)


def print_table(rows: list[dict[str, Any]], with_baseline: bool = True) -> None:
    all_rows = (RAGTEST_BASELINE if with_baseline else []) + rows

    header = "".join(
        (label.ljust(width) if key == "experiment" else label.rjust(width)) + " "
        for key, label, width in COLUMNS
    )
    print(header)
    print("-" * len(header))
    for row in all_rows:
        line = ""
        for key, _, width in COLUMNS:
            value = row.get(key)
            line += (str(value).ljust(width) if key == "experiment" else _fmt(value, width)) + " "
        note = row.get("note") or ""
        print(line + ("  " + note if note else ""))


def write_comparison(rows: list[dict[str, Any]]) -> Path:
    out = RESULTS_DIR / "comparison.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def subtype_table(rows: list[dict[str, Any]]) -> None:
    """하위유형별 정확도. 레인 설계가 어디에 먹혔는지 보려면 이 표를 봅니다."""

    subtypes: list[str] = []
    for row in rows:
        for key in (row.get("subtype_accuracy") or {}):
            if key not in subtypes:
                subtypes.append(key)
    if not subtypes:
        return

    print()
    print("하위유형별 정확도")
    width = max(len(s) for s in subtypes) + 2
    header = "실험".ljust(26) + "".join(s.rjust(max(8, len(s) + 1)) for s in subtypes)
    print(header)
    print("-" * len(header))
    for row in rows:
        line = str(row.get("experiment", ""))[:25].ljust(26)
        acc = row.get("subtype_accuracy") or {}
        for s in subtypes:
            value = acc.get(s)
            line += (f"{value:.2f}" if isinstance(value, float) else "-").rjust(max(8, len(s) + 1))
        print(line)


def router_table(experiment: str) -> None:
    """라우터 정확도 (X1). 골드의 `type` 라벨과 라우터가 고른 레인을 비교합니다."""

    path = RESULTS_DIR / f"{experiment}.jsonl"
    if not path.exists():
        print(f"[라우터] {path} 없음")
        return

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    matrix: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["type"], row["lane"])
        matrix[key] = matrix.get(key, 0) + 1

    correct = sum(v for (gold, pred), v in matrix.items() if gold == pred)
    print()
    print(f"라우터 정확도 (X1, {experiment}): {correct}/{len(rows)} = {correct / len(rows):.3f}")
    print("  골드유형 → 배정레인")
    for (gold, pred), count in sorted(matrix.items()):
        mark = " " if gold == pred else "  ← 불일치"
        print(f"    {gold:<8} → {pred:<8} {count:>3}{mark}")

    mismatched = [r for r in rows if r["type"] != r["lane"]]
    if mismatched:
        print("  불일치 문항:")
        for row in mismatched:
            print(f"    {row['id']} ({row['subtype']}) → {row['lane']} | 정답여부={row['correct']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="실험 결과 비교표")
    parser.add_argument("--router", metavar="EXPERIMENT", help="라우터 혼동표 (X1)")
    parser.add_argument("--only", nargs="*", help="특정 실험만")
    parser.add_argument("--no-baseline", action="store_true")
    parser.add_argument("--subtypes", action="store_true")
    args = parser.parse_args()

    if args.router:
        router_table(args.router)
        return

    rows = load_summaries(args.only)
    print_table(rows, with_baseline=not args.no_baseline)
    if args.subtypes:
        subtype_table(rows)
    path = write_comparison(rows)
    print(f"\n[리포트] {path}")


if __name__ == "__main__":
    main()

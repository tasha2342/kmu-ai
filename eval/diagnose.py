"""진단 리포트 — 손실이 검색에서 났는지 생성에서 났는지 갈라 보여 줍니다.

이 파일이 답하는 질문은 하나입니다: **Recall@12는 0.854인데 정답률은 0.771이다.
그 차이 8.3%p는 어디서 났는가?**

기존 하네스는 이 질문에 답할 수 없었습니다. `scoring.classify_failure()`가
`"<표>"`, `EMPTY_TABLE_WARNING`, `normalize=="unknown"` 같은 날짜/표 레인 전용
휴리스틱으로 **추측**하기 때문입니다. 여기서는 실제로 돌린 4개 arm의 **측정값**에서
유도합니다 (eval/arms.py::classify).

    python3 -m eval.diagnose --experiments M1_4arm
    python3 -m eval.diagnose --experiments M1_4arm M2_title --by-category
"""

from __future__ import annotations

import argparse
import json

from collections import Counter
from pathlib import Path
from typing import Any

from eval.arms import REMEDY
from eval.stats import fmt_ci, wilson


RESULTS_DIR = Path(__file__).resolve().parent / "results"

# 리포트에 싣는 순서. 처방이 상류인 것부터.
FAILURE_ORDER = (
    "ok",
    "retrieval_miss",
    "ranking_miss",
    "selection_miss",
    "generation_miss",
    "gold_defect",
    "lucky_pass",
    "not_evaluated",
)


def load_rows(experiment: str) -> list[dict[str, Any]]:
    path = RESULTS_DIR / f"{experiment}.jsonl"
    if not path.exists():
        raise SystemExit(f"결과 파일이 없습니다: {path}")
    with path.open(encoding="utf-8") as fp:
        return [json.loads(line) for line in fp if line.strip()]


def load_summary(experiment: str) -> dict[str, Any]:
    path = RESULTS_DIR / f"{experiment}_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _pct(n: int, total: int) -> str:
    return f"{n:>4}  ({n / total:6.1%})" if total else "   0  (  0.0%)"


def failure_table(rows: list[dict[str, Any]]) -> str:
    """실패 분류별 건수와 처방."""
    scored = [r for r in rows if r.get("category") != "TRIG"]
    counts = Counter(r.get("failure") or "ok" for r in scored)
    total = len(scored)

    lines = [
        f"{'분류':<18} {'건수':>14}  처방",
        f"{'-' * 18} {'-' * 14}  {'-' * 44}",
    ]
    for cls in FAILURE_ORDER:
        if cls not in counts:
            continue
        lines.append(f"{cls:<18} {_pct(counts[cls], total)}  {REMEDY.get(cls, '')}")
    for cls, n in sorted(counts.items()):
        if cls not in FAILURE_ORDER:
            lines.append(f"{cls:<18} {_pct(n, total)}  (알 수 없는 분류)")
    return "\n".join(lines)


def arm_table(rows: list[dict[str, Any]]) -> str:
    """arm별 성능. 여기서 검색 손실과 생성 손실이 갈립니다."""
    scored = [r for r in rows if r.get("category") != "TRIG"]

    def stat(key: str, label: str, note: str) -> str:
        vals = [r[key] for r in scored if r.get(key) is not None]
        if not vals:
            return f"{label:<28} {'(미실행)':>28}  {note}"
        hits = sum(1 for v in vals if v)
        return f"{label:<28} {fmt_ci(hits, len(vals)):>28}  {note}"

    lines = [
        f"{'Arm':<28} {'값 [95% CI]':>28}  의미",
        f"{'-' * 28} {'-' * 28}  {'-' * 40}",
        stat("recall_article_at_k", "ARM-R  recall_article@k", "검색이 정답 조항을 가져왔는가"),
        stat("recall_doc_at_k", "       recall_doc@k", "문서 단위 (과거 리포트 비교축)"),
        stat("correct_oc", "ARM-OC 오라클(골드만)", "근거를 줬을 때 읽어내는가 = 독해"),
        stat("correct_on", "ARM-ON 오라클+노이즈", "distractor 속에서 고르는가 = 선택"),
        stat("correct", "ARM-E2E 실제 검색", "통합 시스템의 실제 성능"),
    ]
    return "\n".join(lines)


def loss_decomposition(rows: list[dict[str, Any]]) -> str:
    """R → OC → ON → E2E 로 내려가며 어디서 몇 %p씩 빠지는지."""
    scored = [r for r in rows if r.get("category") != "TRIG"]
    n = len(scored)
    if not n:
        return "(채점 대상 문항 없음)"

    def rate(key: str) -> float | None:
        vals = [r[key] for r in scored if r.get(key) is not None]
        return sum(1 for v in vals if v) / len(vals) if vals else None

    r, oc, on, e2e = (rate(k) for k in
                      ("recall_article_at_k", "correct_oc", "correct_on", "correct"))

    lines = ["단계별 손실 (위에서 아래로 내려가며 빠지는 지점)", "-" * 62]
    if oc is not None:
        lines.append(f"  독해 상한 (ARM-OC)                 {oc:6.1%}"
                     f"   ← 골드를 통째로 줘도 못 맞히는 {1 - oc:.1%}는 골드/청크 문제")
    if r is not None:
        lines.append(f"  검색 (ARM-R)                       {r:6.1%}"
                     f"   ← 이 단계에서 {1 - r:.1%} 손실")
    if on is not None and oc is not None:
        lines.append(f"  선택 (ARM-ON)                      {on:6.1%}"
                     f"   ← 독해 대비 {oc - on:+.1%}p (노이즈의 대가)")
    if e2e is not None and on is not None:
        lines.append(f"  최종 (ARM-E2E)                     {e2e:6.1%}"
                     f"   ← 선택 대비 {on - e2e:+.1%}p (순위·예산의 대가)")
    if r is not None and e2e is not None:
        lines.append("")
        lines.append(f"  검색 {r:.1%} → 최종 {e2e:.1%}  =  검색 이후 손실 {r - e2e:+.1%}p")
    return "\n".join(lines)


def category_table(rows: list[dict[str, Any]]) -> str:
    """카테고리별. 백분율이 아니라 x/n 원시값으로 냅니다.

    카테고리당 ~20문항이면 CI가 ±0.20입니다. "장학 정확도 0.75"는 노이즈에
    옷을 입힌 것이고 "장학 15/20"이 정직합니다.
    """
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_cat.setdefault(str(r.get("category")), []).append(r)

    def raw(group: list[dict[str, Any]], key: str) -> str:
        vals = [r[key] for r in group if r.get(key) is not None]
        return f"{sum(1 for v in vals if v)}/{len(vals)}" if vals else "-"

    lines = [
        f"{'카테고리':<8} {'검색':>8} {'최종':>8} {'오라클':>8}  주요 실패",
        f"{'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8}  {'-' * 32}",
    ]
    for cat, group in sorted(by_cat.items()):
        fails = Counter(r["failure"] for r in group if r.get("failure") not in (None, "ok"))
        top = ", ".join(f"{k}×{v}" for k, v in fails.most_common(2)) or "—"
        lines.append(
            f"{cat:<8} {raw(group, 'recall_article_at_k'):>8} "
            f"{raw(group, 'correct'):>8} {raw(group, 'correct_oc'):>8}  {top}"
        )
    return "\n".join(lines)


def gold_defects(rows: list[dict[str, Any]]) -> str:
    """수동 확인이 필요한 문항. 이것부터 처리해야 나머지 수치가 의미를 가집니다."""
    defects = [r for r in rows if r.get("failure") == "gold_defect"]
    if not defects:
        return "gold_defect 없음."
    lines = [f"gold_defect {len(defects)}건 — 골드를 통째로 줘도 틀림. 수동 확인 필요:"]
    for r in defects[:20]:
        lines.append(f"  {r['id']:<10} {r['question'][:44]}")
        lines.append(f"             기대: {str(r.get('gold'))[:70]}")
        lines.append(f"             오라클 응답: {str(r.get('prediction_oc'))[:70]}")
    if len(defects) > 20:
        lines.append(f"  ... 외 {len(defects) - 20}건")
    return "\n".join(lines)


def lucky_passes(rows: list[dict[str, Any]]) -> str:
    """검색은 실패했는데 답은 맞은 문항. 정확도 지표에서 격리해야 합니다."""
    lucky = [r for r in rows if r.get("failure") == "lucky_pass"]
    if not lucky:
        return "lucky_pass 없음."
    lines = [
        f"lucky_pass {len(lucky)}건 — 검색 실패인데 정답. 모델 사전지식 또는 채점기 누출:",
    ]
    for r in lucky[:10]:
        lines.append(f"  {r['id']:<10} {r['question'][:52]}")
    if len(lucky) > 10:
        lines.append(f"  ... 외 {len(lucky) - 10}건")
    lines.append("")
    lines.append("  → 이 문항들을 뺀 정확도가 '검색이 실제로 기여한' 정확도입니다.")
    scored = [r for r in rows if r.get("category") != "TRIG"]
    vals = [r for r in scored if r.get("correct") is not None]
    if vals:
        hits = sum(1 for r in vals if r["correct"])
        honest = hits - len(lucky)
        lines.append(f"    보고값 {fmt_ci(hits, len(vals))}")
        lines.append(f"    격리 후 {fmt_ci(max(0, honest), len(vals))}")
    return "\n".join(lines)


def report(experiment: str, by_category: bool = False) -> str:
    rows = load_rows(experiment)
    summary = load_summary(experiment)

    blocks = [
        f"═══ {experiment} ═══",
        f"인덱스 {summary.get('index')} · 백엔드 {summary.get('backend')} · "
        f"top_k {summary.get('top_k')} · {summary.get('documents')}문서 {summary.get('chunks')}청크",
        f"골드 {summary.get('questions')} · 채점 {summary.get('n_scored')}문항 "
        f"(+ 트리거 대조군 {summary.get('n_trigger_control')}, 헤드라인 제외)",
    ]
    if summary.get("judge"):
        blocks.append(
            f"judge {summary.get('judge_model')} / {summary.get('rubric_version')} · "
            f"호출 {summary.get('judge_calls')}"
        )

    blocks += [
        "",
        arm_table(rows),
        "",
        loss_decomposition(rows),
        "",
        failure_table(rows),
        "",
        gold_defects(rows),
        "",
        lucky_passes(rows),
    ]
    if by_category:
        blocks += ["", category_table(rows)]
    return "\n".join(blocks)


def compare_failures(experiments: list[str]) -> str:
    """여러 실험의 실패 분포를 나란히. 개선이 어느 분류를 줄였는지 봅니다."""
    dists = {e: Counter(r.get("failure") or "ok"
                        for r in load_rows(e) if r.get("category") != "TRIG")
             for e in experiments}
    classes = [c for c in FAILURE_ORDER if any(c in d for d in dists.values())]

    header = f"{'분류':<18}" + "".join(f"{e[:14]:>16}" for e in experiments)
    lines = [header, "-" * len(header)]
    for cls in classes:
        lines.append(f"{cls:<18}" + "".join(f"{dists[e].get(cls, 0):>16}" for e in experiments))
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="4-arm 진단 리포트")
    p.add_argument("--experiments", nargs="+", required=True)
    p.add_argument("--by-category", action="store_true", help="카테고리별 표도 출력")
    args = p.parse_args()

    for exp in args.experiments:
        print(report(exp, by_category=args.by_category))
        print()

    if len(args.experiments) > 1:
        print("═══ 실패 분포 비교 ═══")
        print(compare_failures(args.experiments))


if __name__ == "__main__":
    main()

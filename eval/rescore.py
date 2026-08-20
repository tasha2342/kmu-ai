"""이미 저장된 예측을 다시 채점합니다. 생성을 다시 돌리지 않습니다.

왜 필요한가: 결정적 채점기가 한국어 어미 차이로 정답을 오답 처리합니다.
`text` 정규화기는 부분문자열 비교라 골드 "제한을 두지 않**는다**"와
모델 응답 "제한을 두지 않**습니다**"가 매칭되지 않습니다. L2 실측에서
`exact` 유형 오답 20건이 **전부 실제로는 정답**이었습니다.

생성은 이미 끝났고 응답이 결과 파일에 있으므로, judge로 다시 채점만 하면 됩니다.
KT 노드를 다시 태울 이유가 없습니다.

    export ANTHROPIC_API_KEY=...
    python3 -m eval.rescore --results resources/eval_live/results/L2_prod_4arm.jsonl \
        --questions eval/questions_student.jsonl --experiment L2_judged
"""

from __future__ import annotations

import argparse
import json

from collections import Counter
from pathlib import Path
from typing import Any

from eval.arms import REMEDY
from eval.judge import Judge
from eval.scoring import score_answer_v2
from eval.stats import fmt_ci


# 결과 행의 채점 필드 → 그 arm의 응답이 저장된 필드
# eval.judge_prep.ARM_FIELDS와 같아야 합니다. 어긋나면 판정해 놓고 반영하지 않는 arm이 생깁니다.
ARM_FIELDS = {
    "correct": "prediction",
    "correct_oc": "prediction_oc",
    "correct_on": "prediction_on",
}


def classify(r_hit, oc, on, e2e, indexed: bool) -> str:
    """live_scoring_shim.classify_arms와 같은 규칙입니다."""
    if not indexed:
        return "not_indexed"
    if oc is False:
        return "gold_defect"
    if e2e:
        return "lucky_pass" if r_hit is False else "ok"
    if r_hit is False:
        return "retrieval_miss"
    if e2e is None:
        return "not_evaluated"
    if on is False:
        return "selection_miss"
    if on is True:
        return "ranking_miss"
    return "generation_miss"


def main() -> None:
    p = argparse.ArgumentParser(description="저장된 예측을 judge로 재채점")
    p.add_argument("--results", required=True, help="재채점할 *.jsonl")
    p.add_argument("--questions", required=True, help="골드셋 jsonl")
    p.add_argument("--experiment", required=True, help="새 실험 이름")
    p.add_argument("--out", default="eval/results")
    args = p.parse_args()

    gold = {
        json.loads(l)["id"]: json.loads(l)
        for l in Path(args.questions).read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    rows = [
        json.loads(l)
        for l in Path(args.results).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]

    judge = Judge(experiment=args.experiment)
    out_rows: list[dict[str, Any]] = []
    judged_calls: Counter[str] = Counter()   # arm별 judge 호출 수
    has_pred: set[str] = set()               # 응답이 저장돼 있어 재채점이 가능한 arm

    for r in rows:
        q = gold.get(r["id"])
        if not q:
            out_rows.append(r)
            continue

        new = dict(r)
        for field, pred_field in ARM_FIELDS.items():
            pred = r.get(pred_field)
            if pred is None or r.get(field) is None:
                continue
            has_pred.add(field)
            # 이미 정답으로 판정된 것은 judge를 부르지 않습니다.
            # 정규화기의 pass를 뒤집지 않는다는 원칙 그대로입니다.
            if r[field]:
                continue
            judged_calls[field] += 1
            graded = score_answer_v2(q, pred, judge_fn=judge)
            new[field] = bool(graded["correct"])
            new[f"{field}_verdict"] = graded.get("verdict")
            new[f"{field}_judge_reason"] = graded.get("judge_reason")

        # `prediction_on`이 없는 예전 결과 파일(L2 이전)에서는 ON이 재채점되지 않고
        # 결정적 채점 값 그대로 남습니다. 그 경우 ON을 OC·E2E와 나란히 비교하면 안 됩니다.
        # 아래 summary의 `arms_rejudged`가 어떤 arm이 실제로 judge를 거쳤는지 알려줍니다.
        new["failure"] = classify(
            bool(new["recall_article_at_k"]) if new.get("retrieval_applicable") else None,
            new.get("correct_oc"), new.get("correct_on"), new.get("correct"),
            bool(new.get("gold_docs_indexed", True)),
        )
        out_rows.append(new)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"{args.experiment}.jsonl").open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    scored = [r for r in out_rows if r.get("category") != "TRIG"]
    fair = [r for r in scored if r.get("gold_docs_indexed")]

    def b(group, key):
        vals = [r[key] for r in group if r.get(key) is not None]
        if not vals:
            return None
        h = sum(1 for v in vals if v)
        return fmt_ci(h, len(vals))

    summary = {
        "experiment": args.experiment,
        "rescored_from": Path(args.results).name,
        "n_scored": len(scored),
        "n_gold_docs_indexed": len(fair),
        "recall_article_at_k_indexed_only": b(fair, "recall_article_at_k"),
        # 문서 단위는 조항 단위보다 느슨합니다. 둘의 차이가 "문서는 찾았는데 조항을
        # 놓쳤다"의 크기이고, 이 프로젝트에서 반복해 나온 병목이라 함께 냅니다.
        "recall_doc_at_k_indexed_only": b(fair, "recall_doc_at_k"),
        "answer_accuracy_oc_indexed_only": b(fair, "correct_oc"),
        "answer_accuracy_on_indexed_only": b(fair, "correct_on"),
        "answer_accuracy_e2e_indexed_only": b(fair, "correct"),
        "answer_accuracy_e2e_all": b(scored, "correct"),
        # 어떤 arm이 실제로 judge를 거쳤는지. `predictions_saved`가 false인 arm의 수치는
        # 결정적 채점 결과이므로 judge를 거친 arm과 같은 표에 나란히 놓으면 안 됩니다.
        "arms_rejudged": {
            field: {
                "predictions_saved": field in has_pred,
                "judged": judged_calls.get(field, 0),
            }
            for field in ARM_FIELDS
        },
        "failure_counts": dict(Counter(r["failure"] for r in scored if r.get("failure"))),
        **judge.summary(),
    }
    (out_dir / f"{args.experiment}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    print("\n실패 분류별 처방:")
    for cls, n in sorted(summary["failure_counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {cls:16} {n:>4}  {REMEDY.get(cls, '')}")


if __name__ == "__main__":
    main()

"""통계 유틸 — 신뢰구간과 유의성 검정. stdlib만 씁니다.

왜 필요한가: n=48 골드셋에서 Recall 0.90의 95% 신뢰구간은 [0.778, 0.955]입니다.
0.95(CI [0.860, 0.988])와 통계적으로 구분되지 않습니다. 목표를 "0.90 이상, 가능하면
0.95"로 잡았다면, 그 둘을 구분할 수 없는 표본으로 측정한 수치는 의미가 없습니다.

이 모듈이 강제하는 것:
  1. 모든 헤드라인 비율에 Wilson 신뢰구간을 붙인다.
  2. 설정 비교는 평균 차가 아니라 McNemar (b, c)와 정확검정 p로 보고한다.
     같은 문항을 두 설정으로 돌린 쌍체 설계이므로 독립 표본 검정은 틀립니다.

    python3 -m eval.stats --wilson 0.854 48
    python3 -m eval.stats --plan 0.90 0.95
    python3 -m eval.stats --compare M1_4arm M3_qexp --metric recall_article_at_k
"""

from __future__ import annotations

import argparse
import json
import math

from pathlib import Path
from typing import Any, Iterable, Optional


RESULTS_DIR = Path(__file__).resolve().parent / "results"

# 95% 양측. 다른 수준이 필요하면 z를 직접 넘기세요.
Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float, float]:
    """Wilson score 구간.

    정규근사(p ± z·√(p(1-p)/n))를 쓰지 않는 이유: p가 0이나 1에 가까울 때
    구간이 [0,1]을 벗어나고 폭이 0으로 붕괴합니다. Recall 0.95를 다루는
    지금 상황에서 정확히 그 영역입니다.

    Returns:
        (point, low, high)
    """
    if n <= 0:
        return (0.0, 0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, center - margin), min(1.0, center + margin))


def wilson_from_rate(rate: float, n: int, z: float = Z95) -> tuple[float, float, float]:
    """비율만 있고 성공 수가 없을 때. 반올림 오차만큼 부정확합니다."""
    return wilson(round(rate * n), n, z)


def fmt_ci(successes: int, n: int) -> str:
    """헤드라인 표기: `0.910 [0.867, 0.941], n=205`"""
    p, lo, hi = wilson(successes, n)
    return f"{p:.3f} [{lo:.3f}, {hi:.3f}], n={n}"


# ---------------------------------------------------------------------------
# McNemar — 쌍체 이진 결과 비교
# ---------------------------------------------------------------------------


def _binom_sf(k: int, n: int) -> float:
    """P(X >= k), X ~ Binom(n, 0.5). 정확검정용."""
    if k > n:
        return 0.0
    total = sum(math.comb(n, i) for i in range(k, n + 1))
    return total / (2**n)


def mcnemar(a_correct: list[bool], b_correct: list[bool]) -> dict[str, Any]:
    """McNemar 정확검정 (이항, 양측).

    같은 문항 집합을 두 설정으로 돌린 결과를 비교합니다. 중요한 건 평균 차가 아니라
    **불일치 쌍**입니다:
        b = A는 맞고 B는 틀린 문항 수 (회귀)
        c = A는 틀리고 B는 맞은 문항 수 (개선)
    "+0.04 (b=3, c=11, p=0.057)"과 "+0.04 (b=0, c=8, p=0.008)"은 전혀 다른 주장입니다.
    앞은 11개 고치면서 3개를 깨뜨렸고, 뒤는 깨뜨린 것 없이 8개를 고쳤습니다.
    """
    if len(a_correct) != len(b_correct):
        raise ValueError(f"쌍체 비교인데 길이가 다릅니다: {len(a_correct)} vs {len(b_correct)}")

    b = sum(1 for x, y in zip(a_correct, b_correct) if x and not y)
    c = sum(1 for x, y in zip(a_correct, b_correct) if y and not x)
    n_disc = b + c

    if n_disc == 0:
        p_value = 1.0
    else:
        k = max(b, c)
        p_value = min(1.0, 2 * _binom_sf(k, n_disc))

    n = len(a_correct)
    return {
        "n": n,
        "a_rate": sum(a_correct) / n if n else 0.0,
        "b_rate": sum(b_correct) / n if n else 0.0,
        "delta": (sum(b_correct) - sum(a_correct)) / n if n else 0.0,
        "regressed": b,  # A→B에서 깨진 문항
        "improved": c,  # A→B에서 고쳐진 문항
        "discordant": n_disc,
        "p_value": p_value,
        "significant_05": p_value < 0.05,
    }


def bootstrap_ci(
    values: Iterable[float], iterations: int = 10000, seed: int = 20260812
) -> tuple[float, float, float]:
    """비율이 아닌 지표(MRR 등)의 신뢰구간.

    seed를 고정합니다 — 같은 결과 파일에 대해 같은 구간이 나와야 리포트가 재현됩니다.
    (Date.now()/random 계열을 쓰지 않는 것과 같은 이유입니다.)
    """
    import random

    data = [float(v) for v in values]
    n = len(data)
    if n == 0:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        means.append(sum(data[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * iterations)]
    hi = means[int(0.975 * iterations) - 1]
    return (sum(data) / n, lo, hi)


# ---------------------------------------------------------------------------
# 표본 크기 계획
# ---------------------------------------------------------------------------


def plan_table(p_lo: float = 0.90, p_hi: float = 0.95) -> list[dict[str, Any]]:
    """n별로 두 목표치가 구분되는지 보여 줍니다.

    판정 기준: p_hi를 측정했을 때 Wilson **하한**이 p_lo를 넘어야
    "0.90 이상"이라는 주장이 자기 신뢰구간을 버팁니다.
    """
    rows = []
    for n in (48, 100, 150, 200, 205, 300):
        _, lo_lo, lo_hi = wilson_from_rate(p_lo, n)
        _, hi_lo, hi_hi = wilson_from_rate(p_hi, n)
        rows.append(
            {
                "n": n,
                f"CI({p_lo})": f"[{lo_lo:.3f}, {lo_hi:.3f}]",
                f"CI({p_hi})": f"[{hi_lo:.3f}, {hi_hi:.3f}]",
                "구간겹침": lo_hi >= hi_lo,
                f"{p_hi} 하한이 {p_lo} 초과": hi_lo > p_lo,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 결과 파일 비교
# ---------------------------------------------------------------------------


def _load_rows(experiment: str) -> dict[str, dict[str, Any]]:
    path = RESULTS_DIR / f"{experiment}.jsonl"
    if not path.exists():
        raise SystemExit(f"결과 파일이 없습니다: {path}")
    rows = {}
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = r
    return rows


def _metric_bool(row: dict[str, Any], metric: str) -> Optional[bool]:
    v = row.get(metric)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return bool(v >= 1.0) if isinstance(v, (int, float)) else bool(v)


def compare(experiment_a: str, experiment_b: str, metric: str) -> dict[str, Any]:
    """두 실험을 공통 문항에 대해 쌍체 비교합니다."""
    ra, rb = _load_rows(experiment_a), _load_rows(experiment_b)
    common = sorted(set(ra) & set(rb))

    a_vals, b_vals, ids = [], [], []
    for qid in common:
        va, vb = _metric_bool(ra[qid], metric), _metric_bool(rb[qid], metric)
        if va is None or vb is None:  # 기권 문항 등 해당 없음
            continue
        a_vals.append(va)
        b_vals.append(vb)
        ids.append(qid)

    result = mcnemar(a_vals, b_vals)
    result.update(
        {
            "experiment_a": experiment_a,
            "experiment_b": experiment_b,
            "metric": metric,
            "questions_a_only": sorted(set(ra) - set(rb)),
            "questions_b_only": sorted(set(rb) - set(ra)),
            "skipped_not_applicable": len(common) - len(ids),
            "regressed_ids": [q for q, x, y in zip(ids, a_vals, b_vals) if x and not y],
            "improved_ids": [q for q, x, y in zip(ids, a_vals, b_vals) if y and not x],
            "a_ci": fmt_ci(sum(a_vals), len(a_vals)),
            "b_ci": fmt_ci(sum(b_vals), len(b_vals)),
        }
    )
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="신뢰구간과 쌍체 유의성 검정")
    p.add_argument("--wilson", nargs=2, metavar=("RATE_OR_COUNT", "N"),
                   help="Wilson 구간. 첫 인자가 1 미만이면 비율로 봅니다")
    p.add_argument("--plan", nargs=2, type=float, metavar=("P_LO", "P_HI"),
                   help="두 목표치를 구분하는 데 필요한 표본 크기 표")
    p.add_argument("--compare", nargs=2, metavar=("EXP_A", "EXP_B"), help="두 실험 쌍체 비교")
    p.add_argument("--metric", default="recall_article_at_k", help="--compare에 쓸 지표")
    args = p.parse_args()

    if args.wilson:
        raw, n = float(args.wilson[0]), int(args.wilson[1])
        succ = round(raw * n) if raw <= 1.0 else int(raw)
        print(fmt_ci(succ, n))
    elif args.plan:
        rows = plan_table(*args.plan)
        cols = list(rows[0])
        print(" | ".join(f"{c}" for c in cols))
        print("-|-".join("-" * len(c) for c in cols))
        for r in rows:
            print(" | ".join(str(r[c]) for c in cols))
    elif args.compare:
        print(json.dumps(compare(*args.compare, args.metric), ensure_ascii=False, indent=2))
    else:
        p.print_help()


if __name__ == "__main__":
    main()

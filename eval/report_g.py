"""실측 결과 → 문서 §G에 붙일 마크다운.

§G가 스스로 정한 세 규칙을 코드로 강제합니다. 손으로 표를 만들면 바쁠 때 규칙이 먼저
무너집니다.

1. 헤드라인 수치에는 Wilson 신뢰구간을 붙인다 (맨 `0.91`을 쓰지 않는다).
2. 카테고리별은 백분율이 아니라 `x/n` 원시값으로 쓴다 (카테고리당 20문항이면 CI가 ±0.20).
3. 설정 비교는 평균 차가 아니라 McNemar `(b, c)`와 p를 함께 본다.

추가로 §G가 "격리 전후를 둘 다 보고한다"고 적어 둔 `lucky_pass`를 실제로 분리해서 냅니다.
검색이 실패했는데 사전지식으로 맞힌 문항을 정확도에 넣으면 검색 품질이 과대평가됩니다.

    PYTHONPATH=. python3 -m eval.report_g --experiment eval/results/L3_judged.jsonl \
        --summary eval/results/L3_judged_summary.json \
        --compare eval/results/L3_k12_judged.jsonl --compare-label "k=12"
"""

from __future__ import annotations

import argparse
import json

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from eval.arms import REMEDY
from eval.stats import fmt_ci, mcnemar


# §G "채울 표"의 행 순서. 여기서 벗어나면 문서와 표가 어긋납니다.
ARM_ROWS = [
    ("ARM-R", "recall_article_at_k", "검색이 정답 조항을 가져왔는가"),
    ("ARM-R", "recall_doc_at_k", "문서 단위 (과거 0.854와 비교축)"),
    ("ARM-OC", "correct_oc", "오라클(골드 청크만) = **독해**"),
    ("ARM-ON", "correct_on", "오라클 + distractor = **선택**"),
    ("ARM-E2E", "correct", "실제 top-k — 통합 시스템의 실제 성능"),
]

FAILURE_ORDER = [
    "gold_defect",
    "retrieval_miss",
    "selection_miss",
    "ranking_miss",
    "lucky_pass",
    "generation_miss",
    "not_indexed",
    "not_evaluated",
    "ok",
]


def load(path: str) -> list[dict[str, Any]]:
    return [
        json.loads(l)
        for l in Path(path).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


def ci(rows: list[dict[str, Any]], key: str) -> Optional[str]:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return fmt_ci(sum(1 for v in vals if v), len(vals))


def xn(rows: list[dict[str, Any]], key: str) -> str:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return "—"
    return f"{sum(1 for v in vals if v)}/{len(vals)}"


def main() -> None:
    p = argparse.ArgumentParser(description="§G 마크다운 생성")
    p.add_argument("--experiment", required=True, help="채점 완료된 *.jsonl")
    p.add_argument("--summary", help="같은 실험의 *_summary.json (출처 표기용)")
    # `--experiment`는 **지금 보고하는 구성**(보통 최신), `--compare`는 **비교 기준선**입니다.
    # 이 방향이 헷갈리면 McNemar의 b/c가 뒤집혀 "고친 것"과 "깬 것"이 바뀝니다.
    p.add_argument("--compare", help="비교 기준선 *.jsonl (이전 구성)")
    p.add_argument("--compare-label", default="기준선")
    p.add_argument("--label", default="현재 구성")
    args = p.parse_args()

    rows = load(args.experiment)
    scored = [r for r in rows if r.get("category") != "TRIG"]
    fair = [r for r in scored if r.get("gold_docs_indexed")]

    out: list[str] = []
    w = out.append

    w("## G. 측정 결과\n")
    w(f"- 결과 파일: `{Path(args.experiment).name}`")
    w(f"- 전체 {len(rows)}문항 · 채점 대상 {len(scored)}문항(TRIG 제외) · "
      f"골드 문서 색인분 {len(fair)}문항")

    summary: dict[str, Any] = {}
    if args.summary and Path(args.summary).exists():
        summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
        # rescore가 낸 요약에는 실행 환경 정보가 없습니다. 그때는 줄을 만들지 않습니다 —
        # `top_k = None`이 찍히면 문서를 읽는 사람이 설정을 오해합니다.
        if summary.get("top_k") is not None:
            w(f"- top_k = {summary['top_k']} · 생성 모델 `{summary.get('generation_model')}`")
        if summary.get("indexed_documents") is not None:
            w(f"- 색인 문서 {summary['indexed_documents']}건")
        if summary.get("rescored_from"):
            w(f"- 재채점 원본: `{summary['rescored_from']}` "
              "(실행 설정은 그 파일의 `*_summary.json` 참조)")

    # ── arm 표 ──────────────────────────────────────────────────────────────
    w("\n### Arm별 지표\n")
    w("분모는 **골드 문서가 색인된 문항**입니다. 미색인 문항은 검색이 못 찾는 게 당연해서")
    w("섞으면 검색 품질이 아니라 코퍼스 적재율을 재게 됩니다.\n")
    w("| Arm | 지표 | 값 [95% CI] | 의미 |")
    w("| --- | --- | --- | --- |")
    for arm, key, meaning in ARM_ROWS:
        w(f"| {arm} | `{key}` | {ci(fair, key) or '—'} | {meaning} |")

    # judge를 거치지 않은 arm은 같은 표에서 비교하면 안 됩니다.
    rejudged = (summary.get("arms_rejudged") or {}) if summary else {}
    stale = [f for f, v in rejudged.items() if not v.get("predictions_saved")]
    if stale:
        w("\n> ⚠ **위 표에서 " + ", ".join(f"`{f}`" for f in stale) + " 은 judge를 거치지 않았습니다.**")
        w("> 응답 텍스트가 결과 파일에 저장되지 않아 재채점할 원본이 없습니다. 이 값은")
        w("> 결정적 채점 결과이고, 한국어 어미 차이로 인한 false negative를 그대로 안고")
        w("> 있습니다. judge를 거친 다른 arm과 나란히 비교하지 마십시오.")

    # ── lucky_pass 격리 ─────────────────────────────────────────────────────
    lucky = [r for r in fair if r.get("failure") == "lucky_pass"]
    w("\n### lucky_pass 격리\n")
    if lucky:
        clean = [r for r in fair if r.get("failure") != "lucky_pass"]
        w(f"검색이 실패했는데 정답을 낸 문항이 **{len(lucky)}건**입니다. 사전지식이나 누출이므로")
        w("검색 품질 측정에서 빼고 본 값을 함께 적습니다.\n")
        w("| 기준 | ARM-E2E |")
        w("| --- | --- |")
        w(f"| 격리 전 (전체 {len(fair)}문항) | {ci(fair, 'correct') or '—'} |")
        w(f"| 격리 후 ({len(clean)}문항) | {ci(clean, 'correct') or '—'} |")
        w("\n해당 문항: " + ", ".join(f"`{r['id']}`" for r in lucky))
    else:
        w("`lucky_pass` 0건입니다. 검색이 실패한 문항 중 정답을 낸 것이 없어, 격리 전후가 같습니다.")

    # ── 실패 분류 ───────────────────────────────────────────────────────────
    w("\n### 실패 분류\n")
    counts = Counter(r["failure"] for r in scored if r.get("failure"))
    w("| 실패 분류 | 건수 | 처방 |")
    w("| --- | --- | --- |")
    for cls in FAILURE_ORDER:
        if counts.get(cls):
            w(f"| `{cls}` | {counts[cls]} | {REMEDY.get(cls, '')} |")
    for cls, n in counts.items():
        if cls not in FAILURE_ORDER:
            w(f"| `{cls}` | {n} | {REMEDY.get(cls, '')} |")

    # ── 카테고리별 (백분율 금지) ────────────────────────────────────────────
    w("\n### 카테고리별 — 원시값\n")
    w("카테고리당 문항이 적어 백분율은 노이즈에 옷을 입힌 것입니다. `x/n`으로만 씁니다.\n")
    w("| 카테고리 | n | ARM-R | ARM-OC | ARM-ON | ARM-E2E |")
    w("| --- | --- | --- | --- | --- | --- |")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in fair:
        groups[r.get("category") or "(없음)"].append(r)
    for cat in sorted(groups):
        g = groups[cat]
        w(f"| {cat} | {len(g)} | {xn(g,'recall_article_at_k')} | {xn(g,'correct_oc')} "
          f"| {xn(g,'correct_on')} | {xn(g,'correct')} |")

    # ── 대조군 ──────────────────────────────────────────────────────────────
    w("\n### 대조군 (헤드라인 제외)\n")
    trig = [r for r in rows if r.get("category") == "TRIG"]
    neg = [r for r in scored if r.get("answer_type") == "abstain"]
    w("| 대조군 | 무엇을 보나 | 통과 |")
    w("| --- | --- | --- |")
    if trig:
        w(f"| TRIG ({len(trig)}문항) | 구어·오타 질의에도 검색이 정답 조항을 가져오나 "
          f"| {xn(trig, 'recall_article_at_k')} |")
    if neg:
        w(f"| NEG ({len(neg)}문항) | 근거 없는 질문에 지어내지 않고 기권하나 "
          f"| {xn(neg, 'correct')} |")

    # ── McNemar ─────────────────────────────────────────────────────────────
    if args.compare:
        other = {r["id"]: r for r in load(args.compare)}
        w(f"\n### 설정 비교 — {args.compare_label} → {args.label}\n")
        w("평균 차만 보면 몇 개를 고치면서 몇 개를 깨뜨렸는지가 보이지 않습니다.")
        w(f"`b`는 **{args.compare_label}만** 맞힌 수(= 바꾸면서 **깨진** 문항), "
          f"`c`는 **{args.label}만** 맞힌 수(= **고쳐진** 문항)입니다.")
        w("`차이`는 현재 구성 − 기준선이고, `*`는 p < 0.05입니다.\n")
        w(f"| 지표 | {args.compare_label} | {args.label} | 차이 (b, c) | p |")
        w("| --- | --- | --- | --- | --- |")
        for _, key, _ in ARM_ROWS:
            pairs = [(r, other[r["id"]]) for r in fair
                     if r["id"] in other
                     and r.get(key) is not None
                     and other[r["id"]].get(key) is not None]
            if not pairs:
                continue
            cur = [bool(x.get(key)) for x, _ in pairs]
            old = [bool(y.get(key)) for _, y in pairs]
            # `mcnemar(A, B)`는 A→B 방향으로 regressed=b(깨짐), improved=c(고침)를 셉니다.
            # 기준선을 A, 현재 구성을 B로 넣어야 b/c가 "깨진 것 / 고친 것"으로 읽힙니다.
            m = mcnemar(old, cur)
            w(f"| `{key}` | {sum(old)}/{len(old)} | {sum(cur)}/{len(cur)} "
              f"| {m['delta']:+.3f} (b={m['regressed']}, c={m['improved']}) "
              f"| {m['p_value']:.4f}{' *' if m['significant_05'] else ''} |")

    # ── 출처 ────────────────────────────────────────────────────────────────
    w("\n### 판정 출처\n")
    if summary:
        w(f"- judge 모델: `{summary.get('judge_model')}` · 루브릭 `{summary.get('rubric_version')}`")
        calls = summary.get("judge_calls") or {}
        w(f"- judge 호출: 캐시 {calls.get('cached', 0)} · 신규 {calls.get('fresh', 0)} "
          f"· 오류 {calls.get('error', 0)}")
    w("- **LLM-judge κ: 미측정.** `judge.calibrate()`는 사람이 라벨한 "
      "`human_verdict` 쌍을 요구하는데 아직 없습니다. §G가 정한 \"κ 0.8 미만이면 judge "
      "수치 전체 폐기\" 게이트를 **통과한 것이 아니라 아직 통과하지 못한 상태**입니다.")

    print("\n".join(out))


if __name__ == "__main__":
    main()

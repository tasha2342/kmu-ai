"""Deterministic scoring for date/table RAG answers.

아래쪽 "학생 골드셋(v2)" 절에 조항 단위 검색 지표와 자유형 답변 채점이 추가되어 있습니다.
기존 6개 함수(normalize_*, score_answer, classify_failure, score_retrieval)는
48문항 회귀 기준점이 걸려 있으므로 **수정하지 않습니다.**
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Optional


_DATE_PATTERNS = [
    re.compile(
        r"(?P<y>19\d{2}|20\d{2})\s*[.\-/년]\s*(?P<m>\d{1,2})\s*[.\-/월]\s*(?P<d>\d{1,2})\s*일?"
    ),
    re.compile(r"(?P<y>19\d{2}|20\d{2})(?P<m>\d{2})(?P<d>\d{2})"),
]


def normalize_date(text: str) -> str | None:
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("+", " ")
    for pat in _DATE_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def normalize_number(text: str) -> str | None:
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text).replace(",", "").replace(" ", "")
    m = re.search(r"\d+(?:\.\d+)?", t)
    return m.group(0) if m else None


def normalize_percent(text: str) -> str | None:
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text)
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return f"{m.group(1)}%"
    # "80퍼센트" style
    m = re.search(r"(\d+(?:\.\d+)?)\s*퍼\s*센트", t)
    if m:
        return f"{m.group(1)}%"
    return None


def normalize_list(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[·•/|;]", ",", t)
    parts = [p.strip() for p in re.split(r"[,，、\n]", t) if p.strip()]
    cleaned = []
    for p in parts:
        p = re.sub(r"^[0-9]+[\.\)]\s*", "", p)
        cleaned.append(p)
    return cleaned


def normalize_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.lower()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[\"'`“”‘’]", "", t)
    return t


def _contains_any(hay: str, needles: list[str]) -> bool:
    h = normalize_text(hay)
    return any(normalize_text(n) in h for n in needles if n)


def score_answer(question: dict[str, Any], prediction: str) -> dict[str, Any]:
    """Return {correct, matched, normalize, reason}."""
    norm = question.get("normalize", "text")
    gold = question.get("answer", "")
    alts = list(question.get("acceptable_answers") or [])
    candidates = [gold] + [a for a in alts if a != gold]
    pred = prediction or ""

    if norm == "date":
        pred_d = normalize_date(pred)
        gold_ds = {normalize_date(c) for c in candidates}
        gold_ds.discard(None)
        ok = pred_d is not None and pred_d in gold_ds
        return {
            "correct": ok,
            "matched": pred_d,
            "normalize": norm,
            "reason": "date_match" if ok else "date_mismatch",
        }

    if norm == "number":
        pred_n = normalize_number(pred)
        gold_ns = {normalize_number(c) for c in candidates}
        gold_ns.discard(None)
        ok = pred_n is not None and pred_n in gold_ns
        return {
            "correct": ok,
            "matched": pred_n,
            "normalize": norm,
            "reason": "number_match" if ok else "number_mismatch",
        }

    if norm == "percent":
        pred_p = normalize_percent(pred)
        gold_ps = {normalize_percent(c) for c in candidates}
        gold_ps.discard(None)
        # also accept bare number that matches percent gold
        if pred_p is None:
            n = normalize_number(pred)
            if n:
                pred_p = f"{n}%"
        ok = pred_p is not None and pred_p in gold_ps
        if not ok and pred_p:
            # soft: gold text contains the percent
            ok = any(pred_p in unicodedata.normalize("NFKC", c) for c in candidates)
        return {
            "correct": ok,
            "matched": pred_p,
            "normalize": norm,
            "reason": "percent_match" if ok else "percent_mismatch",
        }

    if norm == "list":
        pred_items = set(normalize_list(pred))
        # Prefer the most complete candidate
        best = max((normalize_list(c) for c in candidates), key=len)
        gold_items = set(best)
        # Also try splitting gold by common separators already done
        if not gold_items:
            gold_items = {normalize_text(gold)}
        # Fuzzy: all gold tokens appear in prediction text
        ok = gold_items.issubset(pred_items) or all(
            normalize_text(g) in normalize_text(pred) for g in best
        )
        return {
            "correct": ok,
            "matched": sorted(pred_items),
            "normalize": norm,
            "reason": "list_match" if ok else "list_mismatch",
        }

    if norm == "unknown":
        # Correct if model abstains / says unknown
        abstain_markers = [
            "모름",
            "모릅",
            "모르겠",
            "모르",
            "알 수 없",
            "확인할 수 없",
            "표에 없",
            "정보가 없",
            "포함되어 있지 않",
            "unknown",
            "don't know",
            "do not know",
        ]
        # Incorrect if it invents a concrete salary number (4+ digits)
        invents = bool(re.search(r"\d{3,}", pred)) and not _contains_any(pred, abstain_markers)
        ok = _contains_any(pred, abstain_markers) and not invents
        return {
            "correct": ok,
            "matched": "abstain" if ok else "hallucination_or_miss",
            "normalize": norm,
            "reason": "abstain_ok" if ok else "should_abstain",
        }

    # text: any acceptable answer substring / normalized equality
    pred_n = normalize_text(pred)
    ok = False
    matched = None
    for c in candidates:
        cn = normalize_text(c)
        if not cn:
            continue
        if cn == pred_n or cn in pred_n or pred_n in cn:
            ok = True
            matched = c
            break
    return {
        "correct": ok,
        "matched": matched,
        "normalize": "text",
        "reason": "text_match" if ok else "text_mismatch",
    }


def classify_failure(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    score: dict[str, Any],
    prediction: str,
) -> str | None:
    """Blame extraction / chunking / retrieval / generation when wrong."""
    if score.get("correct"):
        return None

    keywords = question.get("must_retrieve_keywords") or []
    joined = "\n".join(h.get("text", "") for h in hits)
    source_ok = any(
        h.get("doc_id") == question.get("doc_id")
        or (question.get("doc_id") and str(h.get("source", "")).startswith(question["doc_id"]))
        for h in hits
    )
    kw_hit = all(k in joined for k in keywords) if keywords else source_ok

    # Table empty-cell probe: extraction issue if context has <표> but no answer cells
    if question.get("normalize") == "unknown":
        if "<표>" in joined or "별표" in joined:
            return "generation_failure"  # context present but model should abstain
        return "extraction_failure"

    if question.get("type") == "table" and ("<표>" in joined) and not any(
        normalize_number(joined) for _ in [0]
    ):
        # retrieved placeholder table without cell values
        if question.get("subtype") == "cell_value":
            return "extraction_failure"

    if not source_ok or not kw_hit:
        # If gold keywords absent from all hits → retrieval; if split oddly → chunking
        gold_ev = question.get("evidence") or ""
        if gold_ev and gold_ev[:20] not in joined and question.get("article"):
            # article present in corpus but not retrieved cleanly
            return "retrieval_failure"
        return "retrieval_failure"

    # Source retrieved but answer wrong
    if question.get("type") == "date" and normalize_date(joined) and not normalize_date(prediction):
        return "generation_failure"
    if question.get("article") and question["article"] in joined:
        return "generation_failure"
    return "chunking_failure"


def score_retrieval(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    k: int | None = None,
) -> dict[str, Any]:
    """Keyword/doc based retrieval metrics (no labeled chunk ids)."""
    if k is None:
        k = len(hits)
    hits = hits[:k]
    doc_id = question.get("doc_id")
    keywords = question.get("must_retrieve_keywords") or []
    article = question.get("article")

    ranks = []
    for i, h in enumerate(hits, start=1):
        src = str(h.get("source", ""))
        did = h.get("doc_id") or (src.split("학교")[0] if src.startswith("1-0-") else "")
        if doc_id and not (did.startswith(doc_id) or src.startswith(doc_id)):
            continue
        text = h.get("text", "")
        if keywords and not all(k in text or k in src for k in keywords):
            # soft: at least one keyword
            if not any(k in text for k in keywords):
                continue
        if article and article not in text and article not in src:
            # still count doc match with keywords as partial
            pass
        ranks.append(i)
        break

    hit = bool(ranks)
    rr = 1.0 / ranks[0] if ranks else 0.0
    return {"recall_at_k": 1.0 if hit else 0.0, "mrr": rr, "first_rank": ranks[0] if ranks else None}


# =============================================================================
# 학생 골드셋(v2) — 조항 단위 검색 지표 + 자유형 답변 채점
# =============================================================================


def score_retrieval_article(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    k: int | None = None,
) -> dict[str, Any]:
    """조항 단위 검색 지표.

    왜 별도 함수인가: 위의 score_retrieval()은 **문서 단위**입니다. top-k 안에 doc_id가
    맞고 키워드가 하나라도 든 청크가 있으면 만점을 줍니다. 법인 8문서(1-0-1 = 44청크)
    에서는 문제가 없었지만, 학칙 2-0-1은 단일 문서에 239청크입니다. "휴학" 질문이
    휴학을 언급한 아무 학칙 청크나 걸리면 만점을 받아 버려서, 정작 제23조를 못 가져와도
    Recall이 올라갑니다. 학생 문항에서는 이 지표가 부풀려지므로 조항 단위로 잽니다.

    정답 키는 gold_articles의 (doc_id, article)입니다. 부칙을 제외한 3,967개 키 중
    청크 2개 이상으로 쪼개지는 것은 74개(1.9%)뿐이라 사실상 안정적 청크 ID입니다.

    Returns:
        {recall_article_at_k, mrr_article, first_rank_article, matched_articles,
         gold_articles, applicable}
        applicable=False는 기권 문항(정답 조항이 없음)이라 검색 지표 집계에서 빼야 한다는 뜻입니다.
    """
    gold = question.get("gold_articles") or []
    if not gold:
        # 기권 문항: 가져올 정답 조항이 없으므로 Recall 분모에서 제외합니다.
        return {
            "recall_article_at_k": None,
            "mrr_article": None,
            "first_rank_article": None,
            "matched_articles": [],
            "gold_articles": 0,
            "applicable": False,
        }

    if k is None:
        k = len(hits)
    hits = hits[:k]

    wanted = {
        (g.get("doc_id"), unicodedata.normalize("NFC", (g.get("article") or "").strip()))
        for g in gold
    }

    first_rank: Optional[int] = None
    matched: list[str] = []
    for i, h in enumerate(hits, start=1):
        key = (
            h.get("doc_id"),
            unicodedata.normalize("NFC", str(h.get("article") or "").strip()),
        )
        if key not in wanted:
            continue
        label = f"{key[0]} {key[1]}"
        if label not in matched:
            matched.append(label)
        if first_rank is None:
            first_rank = i

    return {
        # 정답 조항 중 하나라도 top-k에 있으면 1.0 (문항 단위 이진 지표).
        "recall_article_at_k": 1.0 if first_rank else 0.0,
        "mrr_article": (1.0 / first_rank) if first_rank else 0.0,
        "first_rank_article": first_rank,
        "matched_articles": matched,
        "gold_articles": len(wanted),
        # 정답 조항이 여러 개인 문항에서 몇 개나 건졌는지. 근거가 흩어진 문항의 진단용입니다.
        "article_coverage": len(matched) / len(wanted) if wanted else 0.0,
        "applicable": True,
    }


def _fact_hits(prediction: str, facts: list[str]) -> tuple[list[str], list[str]]:
    """예측에 사실 항목이 들어 있는지 문자열 단위로 확인합니다.

    required_facts는 사람이 쓴 짧은 요약구라 원문과 표기가 달라질 수 있습니다.
    그래서 이 결과는 **judge를 부를지 정하는 1차 신호**로만 쓰고, 최종 판정은
    judge가 합니다. (score_answer_v2의 두 단 구조 참고)
    """
    found, missing = [], []
    pred = normalize_text(prediction)
    for f in facts:
        # 슬래시·중점으로 이어 쓴 요약구는 조각 중 하나만 맞아도 인정합니다.
        pieces = [p for p in re.split(r"[·/,]| 또는 ", f) if len(normalize_text(p)) >= 2]
        ok = any(normalize_text(p) in pred for p in pieces) if pieces else normalize_text(f) in pred
        (found if ok else missing).append(f)
    return found, missing


def score_answer_v2(
    question: dict[str, Any],
    prediction: str,
    judge_fn: Optional[Callable[[dict[str, Any], str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """answer_type을 반영한 답변 채점. 결정적 채점을 먼저, judge는 보조로 씁니다.

    두 단 구조인 이유:
      - 결정적 채점은 감사 가능하고 무료이며 재현됩니다. 이걸 코어로 둡니다.
      - judge는 (a) 문항이 judge=true로 표시됐거나, (b) 결정적 채점이 틀렸다고 본
        text/list 문항에서만 부릅니다. **정규화기가 맞다고 한 것을 judge가 뒤집지 않습니다.**
        judge가 죽어도 오늘과 같은 동작으로 degrade합니다.

    Args:
        judge_fn: (question, prediction) -> {verdict, reason, ...}. None이면 결정적 채점만.

    Returns:
        {correct, partial, matched, normalize, answer_type, reason, judged, verdict, ...}
    """
    atype = question.get("answer_type", "exact")
    pred = prediction or ""

    # --- 환각 사전 검사: forbidden_facts는 어떤 answer_type에서도 즉시 오답 ---
    forbidden = question.get("forbidden_facts") or []
    if forbidden:
        hit, _ = _fact_hits(pred, forbidden)
        if hit:
            return {
                "correct": False,
                "partial": 0.0,
                "matched": None,
                "normalize": question.get("normalize", "text"),
                "answer_type": atype,
                "reason": "forbidden_fact",
                "forbidden_hit": hit,
                "judged": False,
            }

    # --- 기권 문항: 기존 unknown 정규화기가 이미 정확히 이 일을 합니다 ---
    if atype == "abstain":
        base = score_answer(question, pred)
        out = dict(base, answer_type=atype, partial=1.0 if base["correct"] else 0.0, judged=False)
        # 근거 없이 답을 지어냈는지는 judge가 더 잘 봅니다. 정규화기가 통과시킨 건 건드리지 않습니다.
        if judge_fn and not base["correct"]:
            out = _apply_judge(out, question, pred, judge_fn)
        return out

    # --- 목록형: required_facts 충족률 ---
    if atype == "list_all":
        required = question.get("required_facts") or []
        found, missing = _fact_hits(pred, required)
        ratio = len(found) / len(required) if required else 0.0
        out = {
            "correct": not missing,
            "partial": ratio,
            "matched": found,
            "missing": missing,
            "normalize": question.get("normalize", "text"),
            "answer_type": atype,
            "reason": "all_facts" if not missing else "missing_facts",
            "judged": False,
        }
        if judge_fn and (question.get("judge") or missing):
            out = _apply_judge(out, question, pred, judge_fn)
        return out

    # --- 조건부: 표기 변형이 커서 정규화기로 못 잽니다. judge가 1차 판정자입니다 ---
    if atype == "conditional":
        base = score_answer(question, pred)
        out = dict(
            base,
            answer_type=atype,
            partial=1.0 if base["correct"] else 0.0,
            judged=False,
            reason=base["reason"],
        )
        if judge_fn:
            out = _apply_judge(out, question, pred, judge_fn)
        elif not base["correct"]:
            # judge 없이 돌린 실행에서 조건부 문항이 통째로 오답이 되는 것을 눈에 띄게 표시합니다.
            out["reason"] = "needs_judge"
        return out

    # --- exact / list_any: 기존 정규화기 그대로 ---
    base = score_answer(question, pred)
    out = dict(base, answer_type=atype, partial=1.0 if base["correct"] else 0.0, judged=False)
    if judge_fn and (question.get("judge") or not base["correct"]):
        out = _apply_judge(out, question, pred, judge_fn)
    return out


# judge가 정답으로 인정하는 판정. partial은 부분점수만 주고 정답으로는 세지 않습니다.
_JUDGE_CORRECT = {"correct", "abstain_ok"}


def _apply_judge(
    out: dict[str, Any],
    question: dict[str, Any],
    prediction: str,
    judge_fn: Callable[[dict[str, Any], str], dict[str, Any]],
) -> dict[str, Any]:
    """judge 판정을 결과에 반영합니다. 정규화기의 pass는 절대 뒤집지 않습니다."""
    if out.get("correct"):
        return out
    verdict = judge_fn(question, prediction)
    v = verdict.get("verdict")
    out = dict(out)
    out["judged"] = True
    out["verdict"] = v
    out["judge_reason"] = verdict.get("reason")
    out["judge_matched_facts"] = verdict.get("matched_facts")
    out["judge_missing_facts"] = verdict.get("missing_facts")
    if v in _JUDGE_CORRECT:
        out["correct"] = True
        out["partial"] = 1.0
        out["reason"] = f"judge_{v}"
    elif v == "partial":
        out["partial"] = max(out.get("partial") or 0.0, 0.5)
        out["reason"] = "judge_partial"
    elif v == "hallucination":
        out["partial"] = 0.0
        out["reason"] = "judge_hallucination"
    else:
        out["reason"] = f"judge_{v or 'error'}"
    return out

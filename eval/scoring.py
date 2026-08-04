"""Deterministic scoring for date/table RAG answers."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


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

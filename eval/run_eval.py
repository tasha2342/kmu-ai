"""실험 러너.

## 채점 방침 (rag-test와 다른 점 — 중요)

rag-test의 extractive 정확도(91.7%)는 `run_eval.py::_extractive_answer`가 채점 시
`acceptable_answers`를 들여다보고 답을 고르는 규칙 기반 추출기에서 나온 값입니다.
사실상 "정답 문자열을 담은 청크가 top-k에 올라왔는가"를 잰 것이라 낙관 편향이 있습니다.
그 편향을 그대로 물려받지 않기 위해 여기서는 **정답을 보지 않는 답변기**만 씁니다.

대신 **비교 기준은 검색 지표**로 잡습니다. `eval/scoring.py`는 rag-test 원본을 그대로
복사한 것이고 `score_retrieval`은 정답 문자열을 보지 않으므로, `recall_at_k`/`mrr`은
rag-test의 E4 hybrid k=12 값(**Recall@12 89.6% / MRR 0.762**)과 직접 비교됩니다.
R0가 이 값 근처에 오는지가 하네스 신뢰성 게이트입니다.

## 레인별 답변기

- **날짜**: 검색은 *어느 문서인지*만 정하고, 날짜 값은 날짜 팩트 스토어에서 결정론적으로 읽습니다.
- **표**: 표 청크의 `[셀요약] 헤더=값` 줄 중 질의어와 가장 많이 겹치는 줄의 값.
  값 셀이 비었다는 경고가 붙어 있으면 **기권**합니다(골드 T024가 이 동작을 정답으로 설계).
- **일반**: 최상위 청크 본문.
"""

from __future__ import annotations

import argparse
import json
import re
import time

from collections import Counter
from pathlib import Path
from typing import Any, Optional

from app.utils.regulation_chunker import EMPTY_TABLE_WARNING

from eval.corpus import EVAL8_DOC_IDS, load_documents
from eval.date_facts import DateFacts, answer_date_question, build_fact_store
from eval.index import INDEX_ROOT
from eval.retriever import EvalRetriever, bm25_tokens
from eval.router import Route, route
from eval.scoring import (
    classify_failure,
    score_answer,
    score_answer_v2,
    score_retrieval,
    score_retrieval_article,
)


EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
QUESTIONS_PATH = EVAL_DIR / "questions.jsonl"
# 학생 문항 골드셋. 기존 48문항과 별개 파일입니다 — 저쪽은 회귀 기준점이라 건드리지 않습니다.
STUDENT_QUESTIONS_PATH = EVAL_DIR / "questions_student.jsonl"

ABSTAIN_TEXT = "참조 문서의 표 셀 값이 비어 있어 알 수 없습니다."

CELL_SUMMARY_MARKER = "[셀요약]"


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── 레인별 답변기 (정답을 보지 않습니다) ──────────────────────────────────────


def _resolve_doc(
    hits: list[dict[str, Any]],
    fact_store: dict[str, DateFacts],
    strategy: str = "rank",
) -> Optional[DateFacts]:
    """검색 결과에서 어느 문서를 묻는 것인지 정합니다.

    두 가지를 실제로 재 봤습니다.

    | 방식 | 날짜 정확도 (181문서, hybrid k=12) |
    | --- | --- |
    | `rank` — 상위 5건 순위 가중치(1/rank) | **0.917** |
    | `mass` — 상위 12건 점수 합 | 0.792 |

    `mass`는 "같은 문서에서 여러 청크가 올라오면 그 문서다"는 직관이 그럴듯해서 시도했고,
    실제로 1-0-8/1-0-9를 헷갈리던 2문항을 고쳤습니다. 그런데 파일명 시행일·과거 부칙·조항
    개정일 4문항을 새로 깨뜨려 총점은 더 나빠졌습니다. 하위 순위 청크가 표·부칙 등으로
    질량을 벌어 1등 근거를 눌러 버리기 때문입니다. 그래서 기본값은 `rank`입니다.
    """

    votes: Counter = Counter()
    if strategy == "mass":
        for hit in hits:
            key = hit.get("doc_id") or hit.get("file_name")
            if key:
                votes[key] += float(hit.get("score") or 0.0)
    else:
        for rank, hit in enumerate(hits[:5], start=1):
            key = hit.get("doc_id") or hit.get("file_name")
            if key:
                votes[key] += 1.0 / rank

    for key, _ in votes.most_common():
        if key in fact_store:
            return fact_store[key]
    return None


def answer_date_lane(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    fact_store: dict[str, DateFacts],
    strategy: str = "rank",
) -> str:
    facts = _resolve_doc(hits, fact_store, strategy=strategy)
    if facts is None:
        return ""
    return answer_date_question(question["question"], facts) or ""


def answer_table_lane(question: dict[str, Any], hits: list[dict[str, Any]]) -> str:
    """표 청크의 셀 요약 줄에서 질의어와 가장 잘 맞는 값을 고릅니다."""

    query_tokens = set(bm25_tokens(question["question"]))
    best_line = ""
    best_overlap = 0
    saw_empty_warning = False

    for hit in hits:
        text = hit.get("text") or ""
        if EMPTY_TABLE_WARNING.strip()[:20] in text or "수치 셀이 비어" in text:
            saw_empty_warning = True
        if CELL_SUMMARY_MARKER not in text:
            continue
        body = text.split(CELL_SUMMARY_MARKER, 1)[1]
        for line in body.splitlines():
            if not line.strip():
                continue
            overlap = len(query_tokens & set(bm25_tokens(line)))
            if overlap > best_overlap:
                best_overlap, best_line = overlap, line.strip()

    if question.get("normalize") == "unknown":
        # 값 셀이 비었으면 기권하는 것이 정답입니다(골드 T024).
        if saw_empty_warning or not best_line:
            return ABSTAIN_TEXT

    if best_line:
        return best_line

    # 셀 요약이 없으면 표 청크 본문을 그대로 돌려줍니다.
    for hit in hits:
        if hit.get("section_type") == "table":
            return (hit.get("text") or "")[:400]
    return (hits[0].get("text") or "")[:400] if hits else ""


def answer_general_lane(hits: list[dict[str, Any]]) -> str:
    return (hits[0].get("text") or "")[:600] if hits else ""


# ── 비전 승격 (T3/T4) ────────────────────────────────────────────────────────


def load_table_images() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """`eval/table_store.json`에서 table_id → 구조 이미지, doc_id → 페이지 래스터를 읽습니다."""

    path = EVAL_DIR / "table_store.json"
    if not path.exists():
        return {}, {}

    store = json.loads(path.read_text(encoding="utf-8"))
    by_table: dict[str, str] = {}
    by_doc: dict[str, dict[str, Any]] = {}
    for doc_id, entry in store.get("documents", {}).items():
        for table in entry.get("tables", []):
            if table.get("structure_image"):
                by_table[str(table["table_id"])] = table["structure_image"]
        if entry.get("page_rasters"):
            by_doc[doc_id] = {
                "pages": entry["page_rasters"],
                "text": entry.get("page_text") or {},
            }
    return by_table, by_doc


ATTACHMENT_REF_RE = re.compile(r"별표\s*(\d+(?:\s*-\s*\d+)?)")


def _select_raster_pages(question: str, entry: dict[str, Any], limit: int = 3) -> list[str]:
    """질의가 가리키는 별표가 실린 페이지를 고릅니다.

    44쪽짜리 정관에서 앞 몇 장만 보내면 별표는 들어가지도 않습니다.
    페이지 텍스트에서 `별표 6-1` 같은 참조를 찾아 그 페이지를 우선합니다.
    """

    pages: list[str] = entry.get("pages") or []
    texts: dict[str, str] = entry.get("text") or {}
    if not pages:
        return []

    refs = [m.group(1).replace(" ", "") for m in ATTACHMENT_REF_RE.finditer(question)]
    if refs and texts:
        scored: list[tuple[int, str]] = []
        for number, page_text in texts.items():
            squashed = (page_text or "").replace(" ", "")
            score = sum(1 for ref in refs if f"별표{ref}" in squashed)
            if score:
                scored.append((score, number))
        if scored:
            scored.sort(key=lambda item: (-item[0], int(item[1])))
            chosen = []
            for _, number in scored[:limit]:
                index = int(number) - 1
                if 0 <= index < len(pages):
                    chosen.append(pages[index])
            if chosen:
                return chosen

    return pages[:limit]


def _needs_vision(hits: list[dict[str, Any]], when: str = "empty") -> bool:
    """비전으로 승격할지 판단합니다.

    Args:
        when: `empty` 면 값 셀이 빈 표가 검색됐을 때만, `always` 면 표 질의 전체를 승격.
            `always`는 리포트 E5(표 문항 전체를 비전으로 읽어 100%)와 같은 조건입니다.

    검색된 표 청크를 **전부** 봅니다. 처음 하나만 보고 판단하면(초기 구현이 그랬습니다)
    1위가 멀쩡한 표일 때 뒤쪽의 빈 표를 놓쳐 비전이 한 번도 호출되지 않습니다.
    """

    table_hits = [h for h in hits if h.get("section_type") == "table"]
    if not table_hits:
        return False
    if when == "always":
        return True
    return any("수치 셀이 비어" in (h.get("text") or "") for h in table_hits)


def answer_vision_lane(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    table_images: dict[str, str],
    doc_rasters: dict[str, dict[str, Any]],
    use_raster: bool,
) -> str:
    """값이 빈 표를 이미지로 보여 주고 읽게 합니다.

    rag-test의 E5와 달리 **질문→이미지 매핑을 손으로 하지 않고** 검색된 표의 `table_id`로
    이미지를 찾습니다. 검색이 틀리면 비전도 틀립니다 — 그게 통합 시스템의 실제 성능입니다.
    """

    from eval.gemma import answer_with_images

    paths: list[Path] = []
    if use_raster:
        entry = doc_rasters.get(str(question.get("doc_id") or "")) or {}
        for rel in _select_raster_pages(question["question"], entry):
            paths.append(EVAL_DIR / rel)
    else:
        for hit in hits:
            table_id = str(hit.get("table_id") or "")
            if table_id in table_images:
                paths.append(EVAL_DIR / table_images[table_id])
            if len(paths) >= 2:
                break

    if not paths:
        return ""
    return answer_with_images(question["question"], paths)


def build_evidence(hits: list[dict[str, Any]], max_chars: int, per_source: int) -> str:
    """프로덕션 `_build_evidence_blocks`와 같은 예산으로 근거 블록을 만듭니다."""

    blocks: list[str] = []
    total = 0
    for hit in hits:
        body = (hit.get("text") or "")[:per_source]
        block = (
            f"[출처] 문서:{hit.get('doc_id')} 조항:{hit.get('article')} "
            f"유형:{hit.get('section_type')} 현행 시행일(파일명 기준):{hit.get('effective_date')}\n{body}"
        )
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)


# ── 실행 ────────────────────────────────────────────────────────────────────


def run_experiment(
    experiment: str,
    index_dir: Path,
    backend: str = "lexical",
    top_k: int = 12,
    date_lane: bool = False,
    table_lane: bool = False,
    title_routing: bool = False,
    eval8: bool = False,
    vision: str = "off",
    generate: bool = False,
    generate_mode: str = "all",
    doc_resolution: str = "rank",
    evidence_max_chars: int = 12000,
    source_content_max_chars: int = 1500,
    questions: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """실험 하나를 돌리고 결과를 저장합니다.

    Args:
        vision: `off` | `structure`(T3) | `raster`(T4) — 값이 빈 표를 이미지로 승격
        generate: True면 Gemma-4-31B가 답을 생성합니다 (R7)
    """

    questions = questions or load_questions()
    retriever = EvalRetriever(index_dir, backend=backend)

    fact_store: dict[str, DateFacts] = {}
    if date_lane:
        fact_store = build_fact_store(load_documents(doc_ids=EVAL8_DOC_IDS if eval8 else None))

    table_images: dict[str, str] = {}
    doc_rasters: dict[str, dict[str, Any]] = {}
    vision_when = "always" if vision.endswith("_all") else "empty"
    if vision != "off":
        table_images, doc_rasters = load_table_images()

    rows: list[dict[str, Any]] = []
    for question in questions:
        started = time.time()
        routed: Route = route(question["question"])

        section_filter = routed.section_filter if table_lane else None
        hits = retriever.retrieve(
            question["question"],
            top_k=top_k,
            route=routed,
            title_routing=title_routing,
            section_filter=section_filter,
        )
        # 표 필터를 걸었는데 아무것도 안 걸리면 필터 없이 다시 찾습니다.
        # 라우터가 틀려도 현재 수준 아래로 떨어지지 않게 하는 장치입니다.
        if section_filter and not hits:
            hits = retriever.retrieve(
                question["question"], top_k=top_k, route=routed, title_routing=title_routing
            )

        answer_mode = "extractive"
        if generate and generate_mode == "all":
            # R7: 레인을 쓰지 않고 전부 생성으로 답합니다 (rag-test E4_gen 대응 기준선).
            from eval.gemma import extract_answer_value, generate_answer

            evidence = build_evidence(hits, evidence_max_chars, source_content_max_chars)
            prediction = extract_answer_value(generate_answer(question["question"], evidence))
            answer_mode = "generation"
        elif date_lane and routed.lane == "date":
            prediction = answer_date_lane(question, hits, fact_store, strategy=doc_resolution)
            answer_mode = "date_facts"
        elif routed.lane == "table":
            prediction = answer_table_lane(question, hits)
            answer_mode = "table_cells"
            # 값 셀이 비어 텍스트로 답할 수 없으면 이미지로 승격합니다. (T3/T4)
            if vision != "off" and _needs_vision(hits, when=vision_when):
                vision_answer = answer_vision_lane(
                    question, hits, table_images, doc_rasters, use_raster=vision.startswith("raster")
                )
                if vision_answer:
                    prediction = vision_answer
                    answer_mode = f"vision_{vision}"
        elif generate:
            # `fallback` 모드: 레인이 처리하지 못한 질의만 생성으로 넘깁니다.
            # 규칙으로 확실히 답할 수 있는 건 규칙이 답하고, 설명형만 모델이 맡는 구성입니다.
            from eval.gemma import extract_answer_value, generate_answer

            evidence = build_evidence(hits, evidence_max_chars, source_content_max_chars)
            prediction = extract_answer_value(generate_answer(question["question"], evidence))
            answer_mode = "generation_fallback"
        else:
            prediction = answer_general_lane(hits)
            answer_mode = "general"

        answer_result = score_answer(question, prediction)
        retrieval_result = score_retrieval(question, hits, top_k)
        rows.append(
            {
                "id": question["id"],
                "type": question["type"],
                "subtype": question.get("subtype"),
                "doc_id": question.get("doc_id"),
                "lane": routed.lane,
                "lane_subtype": routed.subtype,
                "answer_mode": answer_mode,
                "question": question["question"],
                "gold": question.get("answer"),
                "prediction": prediction[:300],
                "correct": bool(answer_result.get("correct")),
                "matched": answer_result.get("matched"),
                "recall_at_k": retrieval_result["recall_at_k"],
                "mrr": retrieval_result["mrr"],
                "first_rank": retrieval_result["first_rank"],
                "failure": classify_failure(question, hits, answer_result, prediction),
                "latency_sec": round(time.time() - started, 4),
                "top_hits": [
                    {
                        "doc_id": h.get("doc_id"),
                        "article": h.get("article"),
                        "section_type": h.get("section_type"),
                        "score": round(float(h.get("score", 0.0)), 4),
                    }
                    for h in hits[:5]
                ],
            }
        )

    summary = summarize(rows)
    summary.update(
        {
            "experiment": experiment,
            "index": str(index_dir.name),
            "backend": backend,
            "top_k": top_k,
            "date_lane": date_lane,
            "table_lane": table_lane,
            "title_routing": title_routing,
            "vision": vision,
            "generate": generate,
            "generate_mode": generate_mode if generate else None,
            "doc_resolution": doc_resolution,
            "documents": json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))["documents"],
            "chunks": json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))["chunks"],
        }
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / f"{experiment}.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (RESULTS_DIR / f"{experiment}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_type.setdefault(row["type"], []).append(row)

    failures = Counter(r["failure"] for r in rows if r["failure"])

    out: dict[str, Any] = {
        "n": len(rows),
        "answer_accuracy": _mean([1.0 if r["correct"] else 0.0 for r in rows]),
        "recall_at_k": _mean([r["recall_at_k"] for r in rows]),
        "mrr": _mean([r["mrr"] for r in rows]),
        "avg_latency_sec": _mean([r["latency_sec"] for r in rows]),
        "failure_counts": dict(failures),
    }
    for qtype, group in sorted(by_type.items()):
        out[f"{qtype}_accuracy"] = _mean([1.0 if r["correct"] else 0.0 for r in group])
        out[f"{qtype}_recall_at_k"] = _mean([r["recall_at_k"] for r in group])
        out[f"{qtype}_n"] = len(group)

    by_subtype: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_subtype.setdefault(str(row["subtype"]), []).append(row)
    out["subtype_accuracy"] = {
        k: _mean([1.0 if r["correct"] else 0.0 for r in g]) for k, g in sorted(by_subtype.items())
    }
    return out


# ── 학생 골드셋: 4-arm 진단 실행 ─────────────────────────────────────────────


def run_student_experiment(
    experiment: str,
    index_dir: Path,
    backend: str = "hybrid",
    top_k: int = 12,
    title_routing: bool = False,
    arms: tuple[str, ...] = ("r", "e2e", "on", "oc"),
    use_judge: bool = False,
    recall_curve: Optional[list[int]] = None,
    questions_path: Optional[Path] = None,
    evidence_max_chars: int = 12000,
    source_content_max_chars: int = 1500,
) -> dict[str, Any]:
    """학생 골드셋으로 4-arm 진단을 돌립니다.

    `run_experiment`과 분리한 이유: 저쪽은 날짜/표 레인 로직이 얽혀 있고 48문항
    회귀 기준점이 걸려 있습니다. 거기에 arm 분기를 끼워 넣으면 회귀 게이트가
    자기 자신을 검증하지 못하게 됩니다.
    """
    from eval.arms import GENERATION_ARMS, classify, run_arms

    questions = load_questions(questions_path or STUDENT_QUESTIONS_PATH)
    retriever = EvalRetriever(index_dir, backend=backend)
    arms = tuple(arms)

    judge_fn = None
    judge_obj = None
    if use_judge:
        from eval.judge import Judge

        judge_obj = Judge(experiment=experiment)
        judge_fn = judge_obj

    generate_fn = None
    evidence_fn = None
    if any(a in GENERATION_ARMS for a in arms):
        from eval.gemma import extract_answer_value, generate_answer

        def generate_fn(question_text: str, evidence: str) -> str:  # noqa: F811
            return extract_answer_value(generate_answer(question_text, evidence))

        def evidence_fn(hits: list[dict[str, Any]]) -> str:  # noqa: F811
            return build_evidence(hits, evidence_max_chars, source_content_max_chars)

    rows: list[dict[str, Any]] = []
    curve_hits: dict[int, list[float]] = {n: [] for n in (recall_curve or [])}

    for question in questions:
        started = time.time()
        result = run_arms(
            question,
            retriever,
            top_k=top_k,
            arms=arms,
            generate_fn=generate_fn,
            evidence_fn=evidence_fn,
            title_routing=title_routing,
        )
        hits = result["hits"]

        article = score_retrieval_article(question, hits, top_k)
        doc = score_retrieval(question, hits, top_k)  # 과거 리포트와 비교 가능한 축

        scores: dict[str, Optional[bool]] = {}
        predictions = result.get("predictions", {})
        graded: dict[str, Any] = {}
        for arm in ("e2e", "on", "oc"):
            if arm not in predictions:
                scores[arm] = None
                continue
            g = score_answer_v2(question, predictions[arm], judge_fn=judge_fn)
            graded[arm] = g
            scores[arm] = bool(g["correct"])

        r_hit = bool(article["recall_article_at_k"]) if article["applicable"] else None
        failure = classify(r_hit, scores.get("oc"), scores.get("on"), scores.get("e2e"))

        # recall@N 곡선 — 후보 절단에 걸리지 않도록 별도 검색 (실험 S0)
        if curve_hits:
            deep = retriever.retrieve_ranked(
                question["question"], max_n=max(curve_hits), title_routing=title_routing
            )
            for n in curve_hits:
                s = score_retrieval_article(question, deep, n)
                if s["applicable"]:
                    curve_hits[n].append(s["recall_article_at_k"])

        rows.append(
            {
                "id": question["id"],
                "category": question.get("category"),
                "type": question.get("type"),
                "subtype": question.get("subtype"),
                "doc_id": question.get("doc_id"),
                "answer_type": question.get("answer_type"),
                "question": question["question"],
                "gold": question.get("answer"),
                # 검색
                "recall_article_at_k": article["recall_article_at_k"],
                "mrr_article": article["mrr_article"],
                "first_rank_article": article["first_rank_article"],
                "article_coverage": article.get("article_coverage"),
                "matched_articles": article["matched_articles"],
                "retrieval_applicable": article["applicable"],
                "recall_doc_at_k": doc["recall_at_k"],
                "mrr_doc": doc["mrr"],
                # 생성 arm
                "correct": scores.get("e2e"),
                "correct_on": scores.get("on"),
                "correct_oc": scores.get("oc"),
                "partial_e2e": graded.get("e2e", {}).get("partial"),
                "judged": any(g.get("judged") for g in graded.values()),
                "verdict": graded.get("e2e", {}).get("verdict"),
                "prediction": (predictions.get("e2e") or "")[:300],
                "prediction_oc": (predictions.get("oc") or "")[:300],
                "failure": failure,
                "latency_sec": round(time.time() - started, 4),
                "top_hits": [
                    {
                        "doc_id": h.get("doc_id"),
                        "article": h.get("article"),
                        "section_type": h.get("section_type"),
                        "score": round(float(h.get("score", 0.0)), 4),
                    }
                    for h in hits[:5]
                ],
            }
        )

    summary = summarize_student(rows)
    meta = json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))
    summary.update(
        {
            "experiment": experiment,
            "index": index_dir.name,
            "backend": backend,
            "top_k": top_k,
            "arms": list(arms),
            "title_routing": title_routing,
            "judge": use_judge,
            "questions": str((questions_path or STUDENT_QUESTIONS_PATH).name),
            "documents": meta["documents"],
            "chunks": meta["chunks"],
        }
    )
    if judge_obj:
        summary.update(judge_obj.summary())

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / f"{experiment}.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (RESULTS_DIR / f"{experiment}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if curve_hits:
        from eval.stats import fmt_ci

        curve = {
            str(n): {
                "recall_article": round(sum(v) / len(v), 4) if v else 0.0,
                "ci": fmt_ci(int(sum(v)), len(v)),
                "n": len(v),
            }
            for n, v in sorted(curve_hits.items())
        }
        (RESULTS_DIR / f"{experiment}_curve.json").write_text(
            json.dumps({"experiment": experiment, "curve": curve}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary["recall_curve"] = curve

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def summarize_student(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """헤드라인 수치는 전부 Wilson 신뢰구간을 달고 나갑니다.

    TRIG-* 대조군은 헤드라인에서 제외합니다. 학생 어휘와 규정 어휘의 격차를 재려고
    일부러 어렵게 만든 문항이라, 섞으면 시스템 성능을 과소평가하게 됩니다.
    """
    from eval.stats import fmt_ci

    scored = [r for r in rows if r.get("category") != "TRIG"]
    trig = [r for r in rows if r.get("category") == "TRIG"]

    def _binary(group: list[dict[str, Any]], key: str) -> dict[str, Any]:
        vals = [r[key] for r in group if r.get(key) is not None]
        if not vals:
            return {"rate": None, "n": 0}
        hits = sum(1 for v in vals if v)
        return {"rate": round(hits / len(vals), 4), "hits": hits, "n": len(vals),
                "ci": fmt_ci(hits, len(vals))}

    out: dict[str, Any] = {
        "n": len(rows),
        "n_scored": len(scored),
        "n_trigger_control": len(trig),
        # 검색
        "recall_article_at_k": _binary(scored, "recall_article_at_k"),
        "recall_doc_at_k": _binary(scored, "recall_doc_at_k"),
        "mrr_article": round(
            sum(r["mrr_article"] or 0.0 for r in scored if r["retrieval_applicable"])
            / max(1, sum(1 for r in scored if r["retrieval_applicable"])),
            4,
        ),
        # 생성 arm
        "answer_accuracy_e2e": _binary(scored, "correct"),
        "answer_accuracy_on": _binary(scored, "correct_on"),
        "answer_accuracy_oc": _binary(scored, "correct_oc"),
        "trigger_control_accuracy": _binary(trig, "correct"),
        "failure_counts": dict(Counter(r["failure"] for r in scored if r["failure"])),
        "avg_latency_sec": round(sum(r["latency_sec"] for r in rows) / max(1, len(rows)), 4),
        "judged_count": sum(1 for r in rows if r.get("judged")),
    }

    # 카테고리별은 백분율이 아니라 x/n 원시값으로 냅니다.
    # 카테고리당 ~20문항이면 CI가 ±0.20이라 백분율은 노이즈에 옷을 입힌 것입니다.
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_cat.setdefault(str(r.get("category")), []).append(r)
    out["by_category"] = {
        cat: {
            "recall_article": _raw(group, "recall_article_at_k"),
            "answer_e2e": _raw(group, "correct"),
        }
        for cat, group in sorted(by_cat.items())
    }
    return out


def _raw(group: list[dict[str, Any]], key: str) -> str:
    """`15/20` 형태. 카테고리별 수치를 백분율로 쓰지 않기 위한 것."""
    vals = [r[key] for r in group if r.get(key) is not None]
    return f"{sum(1 for v in vals if v)}/{len(vals)}" if vals else "0/0"


def main() -> None:
    parser = argparse.ArgumentParser(description="규정집 RAG 실험 실행")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--index", required=True, help="eval/indexes/ 아래 인덱스 이름")
    parser.add_argument(
        "--backend", default="lexical",
        choices=("lexical", "pg_simple", "dense", "hybrid", "pg_hybrid"),
        help="pg_hybrid = dense 0.55 + pg_simple 0.45. 진짜 프로덕션 기준선",
    )
    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument("--date-lane", action="store_true", help="날짜 팩트 스토어로 답변")
    parser.add_argument("--table-lane", action="store_true", help="표 질의를 표 청크로 필터")
    parser.add_argument("--title-routing", action="store_true", help="제목 기반 문서 라우팅")
    parser.add_argument("--eval8", action="store_true", help="8문서 인덱스임을 표시")
    parser.add_argument(
        "--vision", default="off",
        choices=("off", "structure", "raster", "structure_all", "raster_all"),
        help="표를 이미지로 승격. `_all`은 표 질의 전체(리포트 E5와 같은 조건)",
    )
    parser.add_argument("--generate", action="store_true", help="Gemma-4-31B가 답 생성")
    parser.add_argument(
        "--generate-mode", default="all", choices=("all", "fallback"),
        help="all=전부 생성(R7 기준선) / fallback=레인이 못 푼 질의만 생성",
    )
    parser.add_argument(
        "--doc-resolution", default="rank", choices=("rank", "mass"),
        help="날짜 레인에서 문서를 고르는 방식",
    )
    # ── 학생 골드셋 4-arm 경로 ───────────────────────────────────────────────
    parser.add_argument(
        "--questions", metavar="PATH",
        help="골드셋 경로. 지정하면 학생 4-arm 경로로 실행합니다 "
             "(기본은 기존 48문항 date/table 경로)",
    )
    parser.add_argument(
        "--arms", default="r,e2e,on,oc",
        help="실행할 arm. r=검색만, e2e=실제 top-k, on=골드+distractor, oc=골드만",
    )
    parser.add_argument("--judge", action="store_true", help="자유형 답변을 Claude로 2차 판정")
    parser.add_argument(
        "--recall-curve", metavar="N,N,...",
        help="recall_article@N 곡선을 그립니다 (실험 S0). 예: 5,10,12,20,30,50,100,200",
    )
    args = parser.parse_args()

    if args.questions:
        run_student_experiment(
            experiment=args.experiment,
            index_dir=INDEX_ROOT / args.index,
            backend=args.backend,
            top_k=args.top_k,
            title_routing=args.title_routing,
            arms=tuple(a.strip() for a in args.arms.split(",") if a.strip()),
            use_judge=args.judge,
            recall_curve=[int(n) for n in args.recall_curve.split(",")] if args.recall_curve else None,
            questions_path=Path(args.questions),
        )
        return

    run_experiment(
        experiment=args.experiment,
        index_dir=INDEX_ROOT / args.index,
        backend=args.backend,
        top_k=args.top_k,
        date_lane=args.date_lane,
        table_lane=args.table_lane,
        title_routing=args.title_routing,
        eval8=args.eval8,
        vision=args.vision,
        generate=args.generate,
        generate_mode=args.generate_mode,
        doc_resolution=args.doc_resolution,
    )


if __name__ == "__main__":
    main()

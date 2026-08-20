"""실제 서비스에 붙여서 도는 평가 — 시뮬레이션이 아닙니다.

`eval/run_eval.py`는 인메모리 인덱스로 프로덕션 검색을 **재현**합니다. 이 파일은
프로덕션 그 자체를 **호출**합니다.

| | run_eval.py | live_eval.py (이 파일) |
| --- | --- | --- |
| 검색 | 인메모리 인덱스 재구현 | `search_regulations()` — 프로덕션 함수 그대로 |
| 데이터 | `eval/indexes/*/chunks.jsonl` | 운영 PostgreSQL `document_chunks_1024` |
| 임베딩 | 인덱스에 미리 넣어둔 벡터 | KURE-v1 (컨테이너 CPU, 프로덕션과 동일) |
| top_k | CLI 인자 | **`collections.top_k` DB 값** (프로덕션 실제 동작) |
| 생성 | 로컬 transformers Gemma | KT 노드 vLLM (프로덕션이 실제로 쓰는 것) |

**반드시 kmu-ai-api 컨테이너 안에서 돌려야 합니다.** KURE-v1 임베더, peewee,
운영 DB 접속 정보가 거기 있고, KT 터널(172.18.0.1)도 컨테이너 게이트웨이입니다.

    docker exec kmu-ai-api python /app/resources/eval_live/live_eval.py \
        --questions /app/resources/eval_live/questions_student.jsonl \
        --experiment L1_prod --arms r,e2e,on,oc --out /app/resources/eval_live/results

`--top-k`를 주지 않으면 **프로덕션이 실제로 쓰는 값**(`collections.top_k`)을 씁니다.
이 값이 지금 5인데 튜닝값은 12라, 그 차이가 실사용에 얼마나 영향을 주는지가
이 하네스로 처음 측정됩니다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.request

from pathlib import Path
from typing import Any, Optional


# 컨테이너에서 /app을 임포트 경로에 올립니다.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")


# KT 노드 vLLM. 터널이 도커 게이트웨이(172.18.0.1)에 바인딩돼 있습니다.
VLLM_BASE = os.environ.get("KMU_EVAL_VLLM", "http://172.18.0.1:59000/v1")
VLLM_MODEL = os.environ.get("KMU_EVAL_MODEL", "")  # 비우면 /v1/models에서 자동 탐색

# Gemma는 thinking을 끌 수 없어 <thought>가 섞여 옵니다. 프로덕션과 같은 방식으로 걷어냅니다.
THOUGHT_OPEN, THOUGHT_CLOSE = "<thought>", "</thought>"

GEN_MAX_TOKENS = 1024
GEN_TIMEOUT = 180


def nfc(s: Any) -> str:
    return unicodedata.normalize("NFC", str(s or "")).strip()


# ---------------------------------------------------------------------------
# 생성 — KT 노드 vLLM
# ---------------------------------------------------------------------------


def discover_model() -> str:
    if VLLM_MODEL:
        return VLLM_MODEL
    with urllib.request.urlopen(f"{VLLM_BASE}/models", timeout=20) as resp:
        return json.load(resp)["data"][0]["id"]


def strip_thought(text: str) -> str:
    """`<thought>...</thought>`를 걷어냅니다.

    Gemma 4는 thinking을 끌 수 없고, OpenAI 호환 경로에서는 사고 과정이 `content` 안에
    인라인으로 들어옵니다. 이걸 그대로 채점하면 사고 과정을 답변으로 채점하게 됩니다.
    닫는 태그만 있고 여는 태그가 없는 경우도 실제로 나오므로 둘 다 처리합니다.
    """
    if THOUGHT_CLOSE in text:
        text = text.rsplit(THOUGHT_CLOSE, 1)[1]
    elif THOUGHT_OPEN in text:
        # 닫는 태그 없이 잘린 응답 — 사고 과정만 온 것이라 답변이 없습니다.
        text = ""
    return text.strip()


SYSTEM_PROMPT = """\
당신은 계명대학교 학생을 돕는 학사·규정 안내 챗봇입니다.

아래 [검색 근거]에 있는 내용만으로 답하십시오. 근거에 없는 내용은 지어내지 말고,
근거가 부족하면 "규정에서 확인할 수 없습니다"라고 답하십시오.
답은 간결하게, 질문에 대한 답부터 말하십시오."""


def generate(model: str, question: str, evidence: str) -> str:
    """KT 노드 vLLM으로 답을 생성합니다."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"[검색 근거]\n{evidence}\n\n[질문]\n{question}",
                },
            ],
            "max_tokens": GEN_MAX_TOKENS,
            "temperature": 0.0,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{VLLM_BASE}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=GEN_TIMEOUT) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        return f"[생성 실패 HTTP {exc.code}] {exc.read()[:200].decode('utf-8', 'replace')}"
    except Exception as exc:  # noqa: BLE001 — 한 문항 실패가 전체를 멈추면 안 됩니다
        return f"[생성 실패] {type(exc).__name__}: {exc}"

    return strip_thought(payload["choices"][0]["message"]["content"] or "")


# ---------------------------------------------------------------------------
# 근거 조립 — 프로덕션 예산 그대로
# ---------------------------------------------------------------------------


def build_evidence(hits: list[dict[str, Any]], max_chars: int = 12000,
                   per_source: int = 1500) -> str:
    """프로덕션 `_build_evidence_blocks`와 같은 예산으로 근거 블록을 만듭니다."""
    blocks, total = [], 0
    for i, h in enumerate(hits, start=1):
        body = (h.get("content") or "")[:per_source]
        block = (
            f"{i}. (관련도 {h.get('score', 0):.3f}) 출처: {h.get('doc_id')} "
            f"{h.get('article')} / 시행일: {h.get('effective_date')}\n원문: {body}"
        )
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 프로덕션 검색
# ---------------------------------------------------------------------------


async def make_context():
    """운영 DB 매니저와 최소 사용자 정보를 만듭니다."""
    from app.models.auth import TokenUserInfo
    from app.utils.database import get_db_manager

    db_manager = await get_db_manager()
    user_info = TokenUserInfo(sub="eval-harness", username="eval", roles=["admin"])
    return db_manager, user_info


async def production_top_k(db_manager) -> Optional[int]:
    """`collections.top_k` — 프로덕션이 실제로 쓰는 값을 읽습니다."""
    from app.models import database as db_models
    from app.utils.regulation_ingest import REGULATION_COLLECTION_NAME

    q = db_models.Collection.select().where(
        db_models.Collection.name == REGULATION_COLLECTION_NAME
    )
    row = await db_manager.select_item(q)
    return getattr(row, "top_k", None) if row else None


async def search(db_manager, user_info, query: str, top_k: Optional[int]) -> list[dict[str, Any]]:
    """프로덕션 `search_regulations()`를 그대로 호출합니다."""
    from app.utils.regulation_ingest import search_regulations

    results, _ms = await search_regulations(db_manager, user_info, query, top_k=top_k)
    return [
        {
            "doc_id": r.doc_id,
            "article": r.article,
            "section_type": r.section_type,
            "effective_date": r.effective_date,
            "content": r.content,
            "score": r.score,
            "file_name": r.file_name,
        }
        for r in results
    ]


async def fetch_gold_chunks(db_manager, gold_articles: list[dict[str, str]]) -> list[dict[str, Any]]:
    """오라클 arm용 — 운영 DB에서 골드 조항의 청크를 직접 꺼냅니다.

    검색을 거치지 않고 DB에서 바로 가져오는 것이 핵심입니다. 그래야 "검색이 실패해도
    근거만 있으면 모델이 답할 수 있는가"를 잴 수 있습니다.
    """
    from app.models import database as db_models
    from app.utils.regulation_ingest import REGULATION_COLLECTION_NAME

    # peewee의 BinaryJSONField 연산자 대신 raw SQL을 씁니다.
    # `->>`(텍스트 추출)가 peewee 표현식으로는 곧바로 나오지 않아, 조용히 0건을
    # 돌려주면 오라클 arm이 통째로 비어 버립니다. SQL을 그대로 쓰는 편이 안전합니다.
    from app.utils.database import get_database

    db = get_database()
    out: list[dict[str, Any]] = []
    for ga in gold_articles:
        cur = db.execute_sql(
            """
            SELECT metadata->>'doc_id', metadata->>'article', metadata->>'section_type',
                   metadata->>'effective_date', content, file_name
            FROM document_chunks_1024
            WHERE collection_name = %s
              AND metadata->>'doc_id' = %s
              AND metadata->>'article' = %s
            ORDER BY chunk_index
            """,
            (REGULATION_COLLECTION_NAME, ga["doc_id"], ga["article"]),
        )
        for doc_id, article, section_type, eff, content, file_name in cur.fetchall():
            out.append(
                {
                    "doc_id": doc_id,
                    "article": article,
                    "section_type": section_type,
                    "effective_date": eff,
                    "content": content,
                    "score": 1.0,
                    "file_name": file_name,
                }
            )
    return out


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


async def run(args) -> None:
    from eval_scoring import classify_arms, score_answer_v2, score_retrieval_article

    questions = [
        json.loads(l) for l in Path(args.questions).read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    needs_gen = any(a in ("e2e", "on", "oc") for a in arms)

    db_manager, user_info = await make_context()
    prod_k = await production_top_k(db_manager)
    top_k = args.top_k if args.top_k else prod_k

    model = discover_model() if needs_gen else "(생성 미사용)"
    print(f"[설정] top_k={top_k} (프로덕션 collections.top_k={prod_k}) · 생성모델={model}",
          file=sys.stderr)

    # 색인 커버리지를 먼저 재 둡니다. 검색 실패를 코퍼스 부재와 구분하기 위한 것입니다.
    coverage = await indexed_docs(db_manager)
    print(f"[색인] 문서 {len(coverage)}개", file=sys.stderr)

    rows: list[dict[str, Any]] = []
    for i, q in enumerate(questions, start=1):
        started = time.time()
        hits = await search(db_manager, user_info, q["question"], top_k)
        art = score_retrieval_article(q, hits, top_k)

        gold_arts = q.get("gold_articles") or []
        # 골드 문서가 애초에 색인돼 있지 않으면 검색이 못 찾는 게 당연합니다.
        # 검색 품질 문제와 코퍼스 부재를 반드시 갈라야 합니다.
        gold_docs = {ga["doc_id"] for ga in gold_arts}
        # 골드 문서가 **없어야 정상인** 문항(abstain/NEG 대조군)은 색인된 것으로 봅니다.
        # `bool(gold_docs) and ...`로 두면 NEG 20문항이 통째로 `not_indexed`가 되어
        # "운영 DB에 문서가 없음 — 인제스트 문제"라는 처방을 달고 채점 분모에서 빠집니다.
        # 근거가 없는 것이 정답인 문항이라 인제스트로 고칠 수 있는 게 아니고, 오히려
        # "지어내지 않고 기권하는가"가 헤드라인에서 사라집니다.
        indexed = gold_docs.issubset(coverage) if gold_docs else True

        # 문서 단위 재현율. 조항 단위(art)보다 느슨하고, 과거 0.854와 이어지는 비교축입니다.
        # 여기서 계산하는 이유는 행에 저장하는 `top_hits`가 5건으로 잘려 있어 사후에
        # k=12 재현율을 복원할 수 없기 때문입니다. 잘리지 않은 `hits`가 여기 있습니다.
        recall_doc = (
            bool({nfc(g) for g in gold_docs} & {nfc(h["doc_id"]) for h in hits})
            if gold_docs
            else None
        )

        preds: dict[str, str] = {}
        if needs_gen:
            if "e2e" in arms:
                preds["e2e"] = generate(model, q["question"], build_evidence(hits))
            if gold_arts:
                gold_chunks = await fetch_gold_chunks(db_manager, gold_arts)
                if gold_chunks:
                    if "oc" in arms:
                        preds["oc"] = generate(model, q["question"], build_evidence(gold_chunks))
                    if "on" in arms:
                        keys = {(nfc(c["doc_id"]), nfc(c["article"])) for c in gold_chunks}
                        distract = [h for h in hits
                                    if (nfc(h["doc_id"]), nfc(h["article"])) not in keys]
                        room = max(0, (top_k or 12) - len(gold_chunks))
                        preds["on"] = generate(
                            model, q["question"],
                            build_evidence(gold_chunks + distract[:room]))

        graded = {a: score_answer_v2(q, p) for a, p in preds.items()}
        sc = {a: bool(g["correct"]) for a, g in graded.items()}
        r_hit = bool(art["recall_article_at_k"]) if art["applicable"] else None

        rows.append({
            "id": q["id"], "category": q.get("category"),
            "answer_type": q.get("answer_type"), "question": q["question"],
            "gold": q.get("answer"),
            "gold_docs_indexed": indexed,
            "recall_article_at_k": art["recall_article_at_k"],
            "recall_doc_at_k": recall_doc,
            "mrr_article": art["mrr_article"],
            "first_rank_article": art["first_rank_article"],
            "matched_articles": art["matched_articles"],
            "retrieval_applicable": art["applicable"],
            "correct": sc.get("e2e"), "correct_on": sc.get("on"), "correct_oc": sc.get("oc"),
            "failure": classify_arms(r_hit, sc.get("oc"), sc.get("on"), sc.get("e2e"), indexed),
            # 세 arm의 응답을 모두 남깁니다. 하나라도 빠지면 그 arm은 재채점할 원본이
            # 없어 결정적 채점 결과에 영구히 묶입니다. L2에서 ON만 저장하지 않는 바람에
            # OC·E2E가 judge로 올라간 뒤에도 ON은 어미 차이 false negative를 그대로 안고
            # 있었고, 세 수치를 나란히 비교할 수 없었습니다.
            "prediction": (preds.get("e2e") or "")[:400],
            "prediction_oc": (preds.get("oc") or "")[:400],
            "prediction_on": (preds.get("on") or "")[:400],
            "latency_sec": round(time.time() - started, 3),
            "top_hits": [{"doc_id": h["doc_id"], "article": h["article"],
                          "score": round(float(h["score"]), 4)} for h in hits[:5]],
        })

        if i % 10 == 0 or i == len(questions):
            print(f"  {i}/{len(questions)}", file=sys.stderr, flush=True)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"{args.experiment}.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = summarize(rows)
    summary.update({
        "experiment": args.experiment, "mode": "live_production",
        "top_k": top_k, "production_collections_top_k": prod_k,
        "generation_model": model, "vllm_base": VLLM_BASE,
        "indexed_documents": len(coverage), "arms": list(arms),
        "questions": Path(args.questions).name,
    })
    (out_dir / f"{args.experiment}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


async def indexed_docs(db_manager) -> set[str]:
    """운영 DB에 실제로 색인된 doc_id 집합."""
    from app.models import database as db_models
    from app.utils.regulation_ingest import REGULATION_COLLECTION_NAME

    from app.utils.database import get_database

    cur = get_database().execute_sql(
        "SELECT DISTINCT metadata->>'doc_id' FROM document_chunks_1024 "
        "WHERE collection_name = %s",
        (REGULATION_COLLECTION_NAME,),
    )
    return {row[0] for row in cur.fetchall() if row[0]}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from eval_scoring import fmt_ci

    scored = [r for r in rows if r.get("category") != "TRIG"]
    # 골드 문서가 색인돼 있는 문항만 — 검색 품질의 정직한 분모입니다.
    fair = [r for r in scored if r.get("gold_docs_indexed")]

    def b(group, key):
        vals = [r[key] for r in group if r.get(key) is not None]
        if not vals:
            return {"rate": None, "n": 0}
        h = sum(1 for v in vals if v)
        return {"rate": round(h / len(vals), 4), "hits": h, "n": len(vals), "ci": fmt_ci(h, len(vals))}

    from collections import Counter
    return {
        "n": len(rows), "n_scored": len(scored),
        "n_gold_docs_indexed": len(fair),
        "n_gold_docs_missing": len(scored) - len(fair),
        "recall_article_at_k_all": b(scored, "recall_article_at_k"),
        "recall_article_at_k_indexed_only": b(fair, "recall_article_at_k"),
        "recall_doc_at_k_indexed_only": b(fair, "recall_doc_at_k"),
        "answer_accuracy_e2e": b(scored, "correct"),
        "answer_accuracy_on": b(scored, "correct_on"),
        "answer_accuracy_oc": b(scored, "correct_oc"),
        "failure_counts": dict(Counter(r["failure"] for r in scored if r["failure"])),
        "avg_latency_sec": round(sum(r["latency_sec"] for r in rows) / max(1, len(rows)), 3),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="프로덕션 실측 평가")
    p.add_argument("--questions", required=True)
    p.add_argument("--experiment", required=True)
    p.add_argument("--arms", default="r")
    p.add_argument("--top-k", type=int, default=None,
                   help="생략하면 프로덕션 collections.top_k를 씁니다")
    p.add_argument("--out", default="/app/resources/eval_live/results")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()

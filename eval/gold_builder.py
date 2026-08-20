"""골드 문항 저작 도구.

빌드된 인덱스(eval/indexes/<NAME>/chunks.jsonl)에서 조문을 꺼내 골드 스켈레톤을 만듭니다.

**.cache의 원문이 아니라 인덱스의 청크를 읽는 것이 중요합니다.** 골드의 gold_spans는
"검색이 실제로 돌려주는 청크 안에 이 구절이 있다"를 뜻해야 하고, 오라클 컨텍스트도
그 청크를 그대로 씁니다. 원문에서 뜬 구절은 청크 경계에서 잘려 있을 수 있습니다.

stdlib만 씁니다 — 이 개발 머신에서 돕니다.

    python3 -m eval.gold_builder --docs                        # 문서 목록
    python3 -m eval.gold_builder --doc 3-4-1 --list            # 조항 목록
    python3 -m eval.gold_builder --doc 3-4-1 --article 제8조    # 조문 전문
    python3 -m eval.gold_builder --search 휴학                  # 키워드가 든 조항 찾기
    python3 -m eval.gold_builder --doc 3-4-1 --article 제8조 --emit JAN-011
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata

from pathlib import Path
from typing import Any, Iterable, Optional


INDEX_ROOT = Path(__file__).resolve().parent / "indexes"
DEFAULT_INDEX = "L_full_typed"

# 청크 맨 앞의 [문서:... ] 메타 접두사. gold_spans는 본문에서만 떠야 하므로 걷어냅니다.
PREFIX_RE = re.compile(r"^\[문서:[^\]]*\]\n?")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def load_chunks(index_name: str = DEFAULT_INDEX) -> list[dict[str, Any]]:
    path = INDEX_ROOT / index_name / "chunks.jsonl"
    if not path.exists():
        raise SystemExit(
            f"인덱스에 chunks.jsonl이 없습니다: {path}\n"
            f"  python3 -m eval.index --name {index_name} --typed-dates 로 먼저 빌드하세요."
        )
    with path.open(encoding="utf-8") as fp:
        return [json.loads(line) for line in fp if line.strip()]


def body_of(chunk: dict[str, Any]) -> str:
    """청크 본문(메타 접두사 제외)."""
    return PREFIX_RE.sub("", chunk.get("text", "")).strip()


def articles_of(chunks: Iterable[dict[str, Any]], doc_id: str) -> list[dict[str, Any]]:
    return [c for c in chunks if c.get("doc_id") == doc_id]


def find_article(
    chunks: Iterable[dict[str, Any]], doc_id: str, article: str
) -> list[dict[str, Any]]:
    """(doc_id, article)에 해당하는 청크를 문서 순서대로. 보통 1개, 드물게 여러 개."""
    want = nfc(article).strip()
    return [c for c in chunks if c.get("doc_id") == doc_id and nfc(c.get("article") or "").strip() == want]


# ---------------------------------------------------------------------------
# 스켈레톤 생성
# ---------------------------------------------------------------------------

# 조문 첫 줄 "제8조(장학생의 자격) ① ..." 에서 제목을 뽑습니다.
ARTICLE_TITLE_RE = re.compile(r"^제\d+조(?:의\d+)?\s*\(([^)]+)\)")

# 항(①②③) 또는 호(1. 2.) 단위로 쪼갭니다. gold_spans 후보로 씁니다.
CLAUSE_SPLIT_RE = re.compile(r"(?=[①-⑳])|(?=\n\s{2,}\d+\.\s)")


def article_title(text: str) -> str:
    m = ARTICLE_TITLE_RE.search(text)
    return m.group(1).strip() if m else ""


def clause_spans(body: str, limit: int = 6) -> list[str]:
    """본문을 항/호 단위로 잘라 gold_spans 후보를 만듭니다.

    저자가 이 중 정답 근거인 것만 남기면 됩니다. 원문에서 그대로 떠 왔으므로
    청크와 바이트 단위로 일치하고, revalidate_gold의 구절 검증을 구조적으로 통과합니다.
    """
    parts = [p.strip() for p in CLAUSE_SPLIT_RE.split(body) if p and p.strip()]
    return [re.sub(r"\s+", " ", p) for p in parts[:limit]]


def keyword_candidates(body: str, title: str) -> list[str]:
    """must_retrieve_keywords 후보. 조문 제목의 명사를 우선 씁니다."""
    words = [w for w in re.split(r"[\s·,()]+", title) if len(w) >= 2]
    return words[:3] or [title[:4]] if title else []


def emit_skeleton(
    qid: str,
    chunks: list[dict[str, Any]],
    category_hint: Optional[str] = None,
) -> dict[str, Any]:
    """골드 레코드 스켈레톤. doc_id/article/gold_spans/evidence는 원문에서 채웁니다."""
    if not chunks:
        raise SystemExit("해당 조항의 청크를 찾지 못했습니다.")

    first = chunks[0]
    body = "\n".join(body_of(c) for c in chunks)
    title = article_title(body)
    prefix = qid.rsplit("-", 1)[0]

    return {
        "id": qid,
        "category": category_hint or prefix,
        "type": "rule",
        "subtype": "",  # 저자가 채움: requirement / procedure / deadline / eligibility ...
        "doc_id": first.get("doc_id"),
        "article": first.get("article"),
        "table_id": first.get("table_id"),
        "question": "",  # ← 저자가 학생 말투로 작성
        "answer": "",  # ← 저자가 작성
        "acceptable_answers": [],
        "answer_type": "exact",
        "normalize": "text",
        "required_facts": [],
        "forbidden_facts": [],
        "unanswerable_reason": None,
        "judge": False,
        "gold_articles": [
            {"doc_id": c.get("doc_id"), "article": c.get("article")} for c in chunks
        ],
        "gold_spans": clause_spans(body),  # ← 정답 근거만 남기고 나머지 삭제
        "evidence": f"{first.get('doc_id')} {first.get('article')}"
        + (f" ({title})" if title else ""),
        "must_retrieve_keywords": keyword_candidates(body, title),
        "notes": "",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_docs(chunks: list[dict[str, Any]]) -> None:
    seen: dict[str, tuple[str, int]] = {}
    for c in chunks:
        did = c.get("doc_id")
        if not did:
            continue
        name, n = seen.get(did, (nfc(c.get("source", "")), 0))
        seen[did] = (name, n + 1)
    for did in sorted(seen, key=lambda d: [int(x) for x in d.split("-")]):
        name, n = seen[did]
        print(f"{did:<10} {n:>4}청크  {name}")


def cmd_list(chunks: list[dict[str, Any]], doc_id: str) -> None:
    rows = articles_of(chunks, doc_id)
    if not rows:
        raise SystemExit(f"문서 {doc_id}를 찾지 못했습니다. --docs 로 목록을 보세요.")
    print(f"# {doc_id}  {nfc(rows[0].get('source',''))}  ({len(rows)}청크)\n")
    for c in rows:
        art = c.get("article") or "-"
        body = body_of(c)
        head = re.sub(r"\s+", " ", body)[:70]
        print(f"{c.get('section_type','?'):<11} {art:<24} {head}")


def cmd_show(chunks: list[dict[str, Any]], doc_id: str, article: str) -> None:
    found = find_article(chunks, doc_id, article)
    if not found:
        raise SystemExit(f"{doc_id} {article} 를 찾지 못했습니다. --list 로 조항명을 확인하세요.")
    for i, c in enumerate(found, start=1):
        tag = f" [{i}/{len(found)}]" if len(found) > 1 else ""
        print(f"--- {doc_id} {c.get('article')}{tag}  (유형:{c.get('section_type')}) ---")
        print(body_of(c))
        print()


def cmd_search(chunks: list[dict[str, Any]], keyword: str, limit: int) -> None:
    """키워드가 든 조항을 찾습니다. 문항을 쓸 조문을 고를 때 씁니다."""
    kw = nfc(keyword)
    hits = 0
    for c in chunks:
        if c.get("section_type") not in ("article", "attachment", "body"):
            continue
        body = body_of(c)
        if kw not in nfc(body):
            continue
        hits += 1
        if hits > limit:
            print(f"... (총 {limit}건 초과, --limit 로 늘리세요)")
            return
        snippet = re.sub(r"\s+", " ", body)
        pos = snippet.find(kw)
        window = snippet[max(0, pos - 45) : pos + 95]
        # section_type=body 청크는 article이 None입니다.
        print(f"{c.get('doc_id') or '-':<9} {c.get('article') or '-':<22} …{window}…")
    if not hits:
        print(f"'{keyword}' 를 찾지 못했습니다.")


def main() -> None:
    p = argparse.ArgumentParser(description="골드 문항 저작 도구")
    p.add_argument("--index", default=DEFAULT_INDEX, help=f"인덱스 이름 (기본 {DEFAULT_INDEX})")
    p.add_argument("--docs", action="store_true", help="문서 목록")
    p.add_argument("--doc", help="문서번호 (예: 3-4-1)")
    p.add_argument("--list", action="store_true", help="해당 문서의 조항 목록")
    p.add_argument("--article", help="조항 (예: 제8조)")
    p.add_argument("--search", help="키워드로 조항 찾기")
    p.add_argument("--limit", type=int, default=40, help="--search 결과 최대 건수")
    p.add_argument("--emit", metavar="ID", help="골드 스켈레톤을 JSONL 한 줄로 출력")
    args = p.parse_args()

    chunks = load_chunks(args.index)

    if args.docs:
        cmd_docs(chunks)
    elif args.search:
        cmd_search(chunks, args.search, args.limit)
    elif args.emit:
        if not (args.doc and args.article):
            p.error("--emit 에는 --doc 과 --article 이 필요합니다")
        rec = emit_skeleton(args.emit, find_article(chunks, args.doc, args.article))
        print(json.dumps(rec, ensure_ascii=False))
    elif args.doc and args.article:
        cmd_show(chunks, args.doc, args.article)
    elif args.doc and args.list:
        cmd_list(chunks, args.doc)
    else:
        p.print_help(sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""평가용 인덱스 빌드.

청킹은 프로덕션 `app.utils.regulation_chunker.chunk_document()`를 그대로 씁니다.
여기서 하는 일은 두 가지뿐입니다.

1. `--typed-dates` 옵션이 켜지면 청크 prefix의 `본문날짜:` 를 **역할이 붙은** 항목으로 교체
   (`조항개정일:` / `부칙시행일:` / `부칙순번:`). 이게 실험 D1입니다.
2. `--table-captions` 가 켜지면 표 청크에 선행 조항/제목을 캡션으로 붙임. 실험 T2입니다.
   리포트의 T003/T006 실패(유사한 정원표끼리 혼동)가 "어느 표인지" 알 수 없어서 생겼습니다.

임베딩은 KURE-v1을 프로덕션 `app.utils.embedder`로 계산해 `embeddings.npy`에 저장합니다.
torch가 없는 환경에서는 이 단계를 건너뛰고 어휘 검색만 돌릴 수 있습니다.
"""

from __future__ import annotations

import argparse
import json
import re

from pathlib import Path
from typing import Any, Optional

from app.utils.regulation_chunker import chunk_document

from eval.corpus import EVAL8_DOC_IDS, Document, load_documents
from eval.date_facts import DateFacts, build_fact_store, typed_prefix_fields


INDEX_ROOT = Path(__file__).resolve().parent / "indexes"

# `[문서:3-1-10 | 파일명시행일:2020-12-15 | 조항:제5조 | 유형:article | 본문날짜:...]` 형태의 첫 줄.
PREFIX_LINE_RE = re.compile(r"^\[(?P<body>[^\]]*)\]\n")

# prefix에서 날짜 관련 항목만 골라 교체하기 위한 키.
DATE_PREFIX_KEYS = ("파일명시행일", "본문날짜")


def _rewrite_prefix_with_typed_dates(chunk: dict[str, Any], facts: DateFacts) -> None:
    """청크 prefix의 날짜 항목을 타입 있는 항목으로 갈아 끼웁니다. (D1)

    `본문날짜:2018-12-03,2021-09-27` → `조항개정일:2018-12-03,2021-09-27`
    부칙 청크면 `부칙시행일:2020-12-15 | 부칙순번:8/8(최신)`

    prefix가 없는 청크(표·문서메타)는 건드리지 않습니다.
    """

    text = chunk.get("text") or ""
    m = PREFIX_LINE_RE.match(text)
    if not m:
        return

    kept = [
        bit
        for bit in (b.strip() for b in m.group("body").split("|"))
        if bit and bit.split(":", 1)[0] not in DATE_PREFIX_KEYS
    ]
    kept.extend(typed_prefix_fields(facts, chunk))

    chunk["text"] = "[" + " | ".join(kept) + "]\n" + text[m.end():]


def _attach_table_captions(chunks: list[dict[str, Any]], text: str) -> None:
    """표 청크에 선행 조항 제목을 캡션으로 붙입니다. (T2)

    표 청크만 보면 "정원표"가 몇 개든 구분이 안 됩니다. 본문에서 그 표를 참조하는
    조문(`별표 3`, `[별표 3]`)을 찾아 조항 제목을 캡션으로 얹어 줍니다.
    """

    # 본문에서 "제N조(제목) ... 별표 K" 형태의 참조를 모읍니다.
    article_titles: dict[str, str] = {}
    for m in re.finditer(r"^제(\d+)조(?:의\d+)?\s*[（(]([^)）]{1,40})[)）]", text, re.MULTILINE):
        article_titles[f"제{m.group(1)}조"] = m.group(2).strip()

    references: dict[str, list[str]] = {}
    for article, title in article_titles.items():
        block_match = re.search(
            rf"^{re.escape(article)}\b.*?(?=^제\d+조|\Z)", text, re.MULTILINE | re.DOTALL
        )
        if not block_match:
            continue
        for ref in re.finditer(r"별표\s*(\d+(?:-\d+)?)", block_match.group(0)):
            references.setdefault(ref.group(1), []).append(f"{article}({title})")

    for chunk in chunks:
        if chunk.get("section_type") != "table":
            continue
        table_id = str(chunk.get("table_id") or "")
        number = re.search(r"(\d+(?:-\d+)?)", table_id)
        caption_bits: list[str] = []
        if number and number.group(1) in references:
            caption_bits = references[number.group(1)][:3]
        if caption_bits:
            chunk["text"] = f"[표캡션] 관련조항: {', '.join(caption_bits)}\n" + (chunk.get("text") or "")
            chunk["caption"] = ", ".join(caption_bits)


def build_chunks(
    docs: list[Document],
    typed_dates: bool = False,
    table_captions: bool = False,
    max_chars: int = 1200,
) -> list[dict[str, Any]]:
    """문서 목록에서 청크 전체를 만듭니다."""

    fact_store = build_fact_store(docs) if typed_dates else {}
    out: list[dict[str, Any]] = []

    for doc in docs:
        chunks = chunk_document(doc.text, doc.tables, doc.file_name, max_chars=max_chars)

        if typed_dates:
            facts = fact_store.get(doc.doc_id or doc.file_name)
            if facts:
                for chunk in chunks:
                    _rewrite_prefix_with_typed_dates(chunk, facts)

        if table_captions:
            _attach_table_captions(chunks, doc.text)

        out.extend(chunks)

    return out


def kure_model_path() -> Path:
    """KURE-v1 가중치 경로.

    프로덕션은 `app.utils.resource_manager.get_model_path()`를 쓰는데, 그 모듈이
    `app.config`를 끌고 오고 `app.config`는 toml·pydantic 설정 전체를 로드합니다.
    평가에 config.yaml이 필요할 이유가 없으므로 경로만 직접 계산합니다.
    (규칙은 동일합니다 — `resources/models/<model_id의 / 를 -- 로>`)
    """

    return Path(__file__).resolve().parent.parent / "resources" / "models" / "nlpai-lab--KURE-v1"


class GpuEmbedder:
    """프로덕션 `LocalEmbedder`의 가중치·풀링 설정을 그대로 쓰되 GPU에서 돌립니다.

    프로덕션은 컨테이너 안 CPU에서 임베딩합니다(`EMBEDDING_TORCH_THREADS=2`). 평가에서는
    5천 청크 × 여러 인덱스를 만들어야 해서 CPU로는 너무 느립니다. **가중치·풀링 방식·
    L2 정규화·최대 길이는 프로덕션 로더에서 그대로 읽으므로 벡터는 동일합니다** —
    바뀌는 건 연산 장치뿐입니다.
    """

    def __init__(self, model_id: str = "nlpai-lab/KURE-v1", device: Optional[str] = None):
        import torch

        from app.utils.embedder import LocalEmbedder

        self._embedder = LocalEmbedder(model_id)
        self._embedder._load_sync()  # 프로덕션 로더 (풀링/정규화/max_seq_length 포함)

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = self._embedder._model.to(self.device).eval()
        self.tokenizer = self._embedder._tokenizer
        self.pooling = self._embedder._pooling

    def encode(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        import torch

        vectors: list[list[float]] = []
        with torch.inference_mode():
            for start in range(0, len(texts), batch_size):
                encoded = self.tokenizer(
                    texts[start : start + batch_size],
                    padding=True,
                    truncation=True,
                    max_length=self.pooling.max_seq_length,
                    return_tensors="pt",
                ).to(self.device)

                hidden = self.model(**encoded).last_hidden_state
                if self.pooling.mode == "cls":
                    pooled = hidden[:, 0]
                else:
                    mask = encoded["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                    pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)

                if self.pooling.normalize:
                    pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)

                vectors.extend(pooled.float().cpu().tolist())
        return vectors


def embed_chunks(chunks: list[dict[str, Any]], batch_size: int = 64) -> Any:
    """KURE-v1로 청크 임베딩을 계산합니다. torch가 있는 환경에서만 동작합니다."""

    import numpy as np  # 지연 import

    embedder = GpuEmbedder()
    print(f"  임베딩 장치: {embedder.device}, 청크 {len(chunks)}개", flush=True)

    vectors: list[list[float]] = []
    for start in range(0, len(chunks), batch_size):
        batch = [c["text"] for c in chunks[start : start + batch_size]]
        vectors.extend(embedder.encode(batch, batch_size=batch_size))
        done = min(start + batch_size, len(chunks))
        if done % (batch_size * 10) == 0 or done == len(chunks):
            print(f"  임베딩 {done}/{len(chunks)}", flush=True)

    return np.asarray(vectors, dtype="float32")


def build_index(
    name: str,
    doc_ids: Optional[tuple[str, ...]] = None,
    typed_dates: bool = False,
    table_captions: bool = False,
    with_tables: bool = False,
    with_embeddings: bool = False,
) -> Path:
    """인덱스 하나를 만들어 eval/indexes/<name>/ 에 저장합니다."""

    docs = load_documents(doc_ids=doc_ids, with_tables=with_tables)
    chunks = build_chunks(docs, typed_dates=typed_dates, table_captions=table_captions)

    out_dir = INDEX_ROOT / name
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    meta = {
        "name": name,
        "documents": len(docs),
        "chunks": len(chunks),
        "typed_dates": typed_dates,
        "table_captions": table_captions,
        "with_tables": with_tables,
        "embedding_model": "nlpai-lab/KURE-v1" if with_embeddings else None,
        "section_type_counts": _count_by(chunks, "section_type"),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if with_embeddings:
        import numpy as np

        vectors = embed_chunks(chunks)
        np.save(out_dir / "embeddings.npy", vectors)

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return out_dir


def _count_by(chunks: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        value = str(chunk.get(key))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="평가 인덱스 빌드")
    parser.add_argument("--name", required=True)
    parser.add_argument("--eval8", action="store_true", help="1-0-x 8문서만")
    parser.add_argument("--typed-dates", action="store_true", help="D1: 타입 있는 날짜 prefix")
    parser.add_argument("--table-captions", action="store_true", help="T2: 표 캡션 부착")
    parser.add_argument("--with-tables", action="store_true", help="hwp5html 표 추출 (pyhwp 필요)")
    parser.add_argument("--with-embeddings", action="store_true", help="KURE-v1 임베딩 (torch 필요)")
    args = parser.parse_args()

    build_index(
        name=args.name,
        doc_ids=EVAL8_DOC_IDS if args.eval8 else None,
        typed_dates=args.typed_dates,
        table_captions=args.table_captions,
        with_tables=args.with_tables,
        with_embeddings=args.with_embeddings,
    )


if __name__ == "__main__":
    main()

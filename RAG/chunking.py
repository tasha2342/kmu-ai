"""Article-aware chunking and metadata helpers for regulation HWP text."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


ARTICLE_SPLIT_RE = re.compile(
    r"(?=^제\d+조(?:의\d+)?\s*[\(（])"
    r"|(?=^부\s*칙)"
    r"|(?=^\[별표[^\]]*\])"
    r"|(?=^제\d+장\b)"
    r"|(?=^제\d+절\b)",
    re.MULTILINE,
)

ARTICLE_HEAD_RE = re.compile(r"^제(?P<n>\d+)조(?:의(?P<sub>\d+))?")
BYELO_HEAD_RE = re.compile(r"^\[별표[^\]]*\]")
BYEICHIK_HEAD_RE = re.compile(r"^부\s*칙")
DATE_IN_TEXT_RE = re.compile(
    r"(19\d{2}|20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?"
)
FILENAME_DATE_RE = re.compile(
    r"시행\+?\s*(\d{4})\.?\s*\+?\s*(\d{1,2})\.?\s*\+?\s*(\d{1,2})"
)


def doc_id_from_filename(filename: str) -> str | None:
    name = unicodedata.normalize("NFC", filename)
    m = re.match(r"(1-0-\d+)", name)
    return m.group(1) if m else None


def parse_filename_effective_date(filename: str) -> str | None:
    name = unicodedata.normalize("NFC", filename)
    m = FILENAME_DATE_RE.search(name)
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _normalize_date_tuple(y: str, m: str, d: str) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def extract_dates(text: str) -> list[str]:
    dates = []
    for m in DATE_IN_TEXT_RE.finditer(text):
        dates.append(_normalize_date_tuple(m.group(1), m.group(2), m.group(3)))
    return dates


def _section_meta(block: str) -> dict[str, Any]:
    first = block.strip().splitlines()[0] if block.strip() else ""
    meta: dict[str, Any] = {
        "article": None,
        "section_type": "body",
        "table_id": None,
    }
    if BYEICHIK_HEAD_RE.match(first):
        meta["section_type"] = "byeolchik"
        meta["article"] = "부칙"
    elif BYELO_HEAD_RE.match(first):
        meta["section_type"] = "byeolpyo"
        meta["article"] = first.strip()
        meta["table_id"] = first.strip()
    elif ARTICLE_HEAD_RE.match(first):
        m = ARTICLE_HEAD_RE.match(first)
        if m.group("sub"):
            meta["article"] = f"제{m.group('n')}조의{m.group('sub')}"
        else:
            meta["article"] = f"제{m.group('n')}조"
        meta["section_type"] = "article"
    elif first.startswith("제") and "장" in first[:8]:
        meta["section_type"] = "chapter"
        meta["article"] = first.strip()[:20]
    elif first.startswith("제") and "절" in first[:8]:
        meta["section_type"] = "section"
        meta["article"] = first.strip()[:20]
    return meta


def fixed_window_chunks(text: str, source: str, chunk_size: int, overlap: int) -> list[dict]:
    text = text.strip()
    chunks = []
    start = 0
    doc_id = doc_id_from_filename(source)
    eff = parse_filename_effective_date(source)
    while start < len(text):
        end = start + chunk_size
        piece = text[start:end].strip()
        if piece:
            chunks.append(
                {
                    "source": source,
                    "text": piece,
                    "doc_id": doc_id,
                    "article": None,
                    "section_type": "fixed",
                    "filename_effective_date": eff,
                    "dates_in_chunk": extract_dates(piece)[:20],
                    "table_id": None,
                }
            )
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def article_chunks(
    text: str,
    source: str,
    max_chars: int = 1200,
    include_date_prefix: bool = False,
) -> list[dict]:
    """Split on 조/부칙/별표/장/절 boundaries; split long blocks further."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    # Ensure leading marker for split
    parts = ARTICLE_SPLIT_RE.split(text)
    # First part may be preamble (title / revision history)
    blocks: list[str] = []
    buf = ""
    for part in parts:
        if not part or not part.strip():
            continue
        # re.split with lookahead keeps delimiters attached to following text when using (?=)
        if ARTICLE_HEAD_RE.match(part.strip()) or BYELO_HEAD_RE.match(part.strip()) or BYEICHIK_HEAD_RE.match(
            part.strip()
        ) or re.match(r"^제\d+[장절]", part.strip()):
            if buf.strip():
                blocks.append(buf.strip())
            buf = part
        else:
            buf += part
    if buf.strip():
        blocks.append(buf.strip())

    doc_id = doc_id_from_filename(source)
    eff = parse_filename_effective_date(source)
    chunks: list[dict] = []

    for block in blocks:
        meta = _section_meta(block)
        pieces = _split_long_block(block, max_chars)
        for piece in pieces:
            text_out = piece
            if include_date_prefix:
                prefix_bits = []
                if doc_id:
                    prefix_bits.append(f"문서:{doc_id}")
                if eff:
                    prefix_bits.append(f"파일명시행일:{eff}")
                if meta["article"]:
                    prefix_bits.append(f"조항:{meta['article']}")
                if meta["section_type"]:
                    prefix_bits.append(f"유형:{meta['section_type']}")
                dates = extract_dates(piece)
                if dates:
                    prefix_bits.append(f"본문날짜:{','.join(dates[:8])}")
                text_out = "[" + " | ".join(prefix_bits) + "]\n" + piece

            chunks.append(
                {
                    "source": source,
                    "text": text_out,
                    "doc_id": doc_id,
                    "article": meta["article"],
                    "section_type": meta["section_type"],
                    "filename_effective_date": eff,
                    "dates_in_chunk": extract_dates(piece)[:20],
                    "table_id": meta["table_id"],
                }
            )
    return chunks


def _split_long_block(block: str, max_chars: int) -> list[str]:
    if len(block) <= max_chars:
        return [block]
    # Prefer hang/항 boundaries ①② or numbered 1. 2.
    markers = list(re.finditer(r"(?=^[①②③④⑤⑥⑦⑧⑨⑩]|^\s*[0-9]+[\.．)]\s)", block, re.MULTILINE))
    if len(markers) >= 2:
        idxs = [m.start() for m in markers] + [len(block)]
        parts = []
        start = 0
        # keep header before first marker with first segment
        for i in range(len(idxs) - 1):
            end = idxs[i + 1]
            if i == 0 and idxs[0] > 0:
                seg = block[start:end]
            else:
                seg = block[idxs[i] : end]
            if seg.strip():
                parts.append(seg.strip())
        # merge tiny parts
        merged: list[str] = []
        for p in parts:
            if merged and len(merged[-1]) + len(p) < max_chars:
                merged[-1] = merged[-1] + "\n" + p
            else:
                merged.append(p)
        out: list[str] = []
        for p in merged:
            if len(p) <= max_chars:
                out.append(p)
            else:
                out.extend(_window_split(p, max_chars, overlap=100))
        return out
    return _window_split(block, max_chars, overlap=100)


def _window_split(text: str, size: int, overlap: int) -> list[str]:
    out = []
    start = 0
    while start < len(text):
        end = start + size
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = end - overlap
    return out


def table_markdown_chunks(tables_json: dict, source: str) -> list[dict]:
    """Turn extracted HTML tables into searchable markdown chunks."""
    doc_id = tables_json.get("doc_id") or doc_id_from_filename(source)
    eff = parse_filename_effective_date(source)
    chunks = []
    for t in tables_json.get("tables") or []:
        rows = t.get("rows") or []
        if not rows:
            continue
        lines = [f"[표 {t.get('table_id')}] 출처:{source}"]
        header = rows[0]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for r in rows[1:]:
            # pad/truncate to header width
            cells = list(r) + [""] * max(0, len(header) - len(r))
            lines.append("| " + " | ".join(cells[: len(header)]) + " |")
        # also emit row-wise facts for better retrieval of cell values
        fact_lines = []
        for r in rows[1:]:
            cells = list(r) + [""] * max(0, len(header) - len(r))
            pairs = []
            for h, c in zip(header, cells):
                if c.strip():
                    pairs.append(f"{h.strip()}={c.strip()}")
            if pairs:
                fact_lines.append("; ".join(pairs))
        text = "\n".join(lines)
        if fact_lines:
            text += "\n[셀요약]\n" + "\n".join(fact_lines)
        if t.get("nonempty_cells", 0) == 0 or (
            sum(1 for r in rows for c in r if c.strip()) <= len(header)
        ):
            text += "\n[주의] 이 표의 수치 셀이 비어 있거나 거의 비어 있습니다. 금액을 추정하지 마세요."

        chunks.append(
            {
                "source": source,
                "text": text,
                "doc_id": doc_id,
                "article": t.get("table_id"),
                "section_type": "table",
                "filename_effective_date": eff,
                "dates_in_chunk": extract_dates(text)[:20],
                "table_id": t.get("table_id"),
            }
        )
    return chunks

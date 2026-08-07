"""표 레인 — 표를 본문과 다른 파이프라인으로 다루고, 값이 비면 비전으로 승격시킵니다.

## 왜 별도 파이프라인인가

`hwp5txt`는 표를 `<표>` 자리표시자로만 남깁니다. 계명대 규정집 181문서에 이 자리표시자가
421곳 있습니다. 즉 표 내용을 묻는 질문은 본문 텍스트만으로는 **구조적으로 답할 수 없습니다.**
`hwp5html`이 표 구조를 복구하면 표 정확도가 45.8% → 87.5%로 올라갔습니다(리포트 E3).

그런데 복구해도 **봉급 금액 그리드는 셀이 비어 있습니다**(이미지/특수필드 추정, 리포트 6.2절).
그래서 값이 빈 표는 이미지로 만들어 비전 모델에게 넘깁니다.

## 이미지 두 갈래

- **(a) 구조 렌더** — 추출된 표 구조를 PIL로 다시 그립니다. 리포트 E5가 이 방식으로 100%를 냈습니다.
  단 **원래 비어 있던 금액은 여기에도 없습니다.** 구조를 시각화한 것이지 원본을 본 게 아닙니다.
  한글 폰트가 없으면 라벨이 tofu(□)로 깨져 정확도가 38.1%까지 떨어집니다 — NotoSansKR 필수.

- **(b) 원본 페이지 래스터** — `hwp5odt` → ODT → `soffice --convert-to pdf` → `pdftoppm` PNG.
  리포트 10.1절이 "다음 실험"으로 남긴 것입니다. 원본 조판을 그대로 보므로
  **추출 단계에서 사라진 금액을 실제로 읽을 수 있습니다.**
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import unicodedata

from pathlib import Path
from typing import Any, Optional

from app.utils.regulation_chunker import _is_value_empty_table

from eval.corpus import REGULATION_DIR, Document, load_documents


EVAL_DIR = Path(__file__).resolve().parent
TABLE_IMAGE_DIR = EVAL_DIR / "table_images"
RASTER_DIR = EVAL_DIR / "page_rasters"
FONT_PATH = EVAL_DIR / "fonts" / "NotoSansKR-Regular.ttf"

# 빈 셀 표시. 비전 모델이 "값이 없다"를 인식할 수 있게 명시적으로 그립니다.
EMPTY_CELL_GLYPH = "∅"

# 렌더 방식이 비전 정확도를 크게 좌우합니다. 두 가지를 실제로 재 봤습니다.
#
#   compact — 좁은 셀(190px), 14자 초과는 `…`로 자름   → 전체 0.771 / 표 0.625 / cell_value 0.706
#   wrapped — 넓은 셀(300px), 줄바꿈으로 전부 표시      → 전체 0.729 / 표 0.542 / cell_value 0.588
#
# 처음에는 `wrapped`가 당연히 나을 거라고 봤습니다. T017의 정답
# "퇴직당시 월 기본급 48% × 정년잔여월수(최대 120개월)"이 잘려서 모델이 "최대 개월 수는
# 이미지에 나타나 있지 않다"고 답했기 때문입니다. 그런데 실제로는 **더 나빠졌습니다.**
# 셀을 넓히면 이미지가 커져 이미지 토큰이 늘고, 17자마다 기계적으로 끊는 줄바꿈이 값을
# 중간에서 쪼개 오히려 읽기 어려워집니다. 잘린 한 문항을 살리려다 여러 문항을 잃었습니다.
#
# 그래서 기본값은 `compact`입니다. 바꾸기 전에 반드시 재세요.
RENDER_STYLE = "compact"

COMPACT = {"cell_width": 190, "line_height": 46, "max_lines": 1, "chars_per_line": 14}
WRAPPED = {"cell_width": 300, "line_height": 24, "max_lines": 4, "chars_per_line": 17}

PADDING = 18
FONT_SIZE = 17


def is_value_empty(table: dict[str, Any]) -> bool:
    """값 셀이 비어 텍스트로는 답할 수 없는 표인지. 프로덕션 판정을 그대로 씁니다."""
    return _is_value_empty_table(table.get("rows") or [], table.get("nonempty_cells"))


def render_table_png(table: dict[str, Any], out_path: Path, style: str = RENDER_STYLE) -> Path:
    """표 구조를 PNG로 그립니다. (a)

    Args:
        style: `compact`(기본, 자름) 또는 `wrapped`(줄바꿈). 위 상수 주석의 측정치 참고.
    """

    spec = COMPACT if style == "compact" else WRAPPED

    from PIL import Image, ImageDraw, ImageFont

    rows: list[list[str]] = table.get("rows") or []
    if not rows:
        raise ValueError("빈 표는 렌더할 수 없습니다.")

    columns = max(len(r) for r in rows)

    per_line = spec["chars_per_line"]
    max_lines = spec["max_lines"]

    def _wrap(value: str) -> list[str]:
        text = (value or "").strip() or EMPTY_CELL_GLYPH
        if max_lines == 1:
            return [text if len(text) <= per_line else text[: per_line - 1] + "…"]
        lines = [text[i : i + per_line] for i in range(0, len(text), per_line)]
        return lines[:max_lines]

    wrapped = [[_wrap(row[c] if c < len(row) else "") for c in range(columns)] for row in rows]
    # compact는 행 높이가 정확히 line_height여야 합니다. 여유 10px을 더하면 이미지 기하가
    # 달라지고 비전 정확도가 실제로 흔들립니다(0.771 → 0.750, 48문항 중 1문항).
    row_padding = 0 if max_lines == 1 else 10
    row_heights = [
        max(1, max(len(cell) for cell in row)) * spec["line_height"] + row_padding
        for row in wrapped
    ]

    width = PADDING * 2 + spec["cell_width"] * columns
    height = PADDING * 2 + spec["line_height"] + sum(row_heights)

    if not FONT_PATH.exists():
        raise FileNotFoundError(
            f"한글 폰트가 없습니다: {FONT_PATH}\n"
            "  폰트 없이 렌더하면 라벨이 □로 깨져 정확도가 38%까지 떨어집니다(리포트 5.3절)."
        )
    font = ImageFont.truetype(str(FONT_PATH), FONT_SIZE)
    title_font = ImageFont.truetype(str(FONT_PATH), FONT_SIZE + 3)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((PADDING, PADDING - 4), str(table.get("table_id") or ""), fill="black", font=title_font)

    y0 = PADDING + spec["line_height"]
    for r, row in enumerate(wrapped):
        cell_height = row_heights[r]
        for c in range(columns):
            x0 = PADDING + c * spec["cell_width"]
            draw.rectangle(
                [x0, y0, x0 + spec["cell_width"], y0 + cell_height], outline="black", width=1
            )
            offset = 12 if max_lines == 1 else 6
            for line_no, line in enumerate(row[c]):
                draw.text(
                    (x0 + 6, y0 + offset + line_no * spec["line_height"]),
                    line, fill="black", font=font,
                )
        y0 += cell_height

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


def render_page_rasters(hwp_path: Path, out_dir: Path, dpi: int = 150) -> tuple[list[Path], dict[str, str]]:
    """HWP 원본을 페이지 이미지로 만듭니다. (b)

    `hwp5odt` → ODT → LibreOffice PDF → `pdftoppm` PNG.
    추출 단계에서 사라진 봉급 금액을 원본 조판 그대로 보기 위한 경로입니다.
    """

    if not shutil.which("soffice"):
        raise RuntimeError("libreoffice(soffice)가 없습니다. apt-get install libreoffice-writer")
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdftoppm이 없습니다. apt-get install poppler-utils")

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        subprocess.run(
            ["hwp5odt", "--output", str(tmp_dir / "doc.odt"), str(hwp_path)],
            check=True,
            capture_output=True,
            timeout=180,
        )
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmp_dir), str(tmp_dir / "doc.odt")],
            check=True,
            capture_output=True,
            timeout=300,
        )
        pdf = tmp_dir / "doc.pdf"
        if not pdf.exists():
            raise RuntimeError(f"PDF 변환 실패: {hwp_path.name}")
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(out_dir / "page")],
            check=True,
            capture_output=True,
            timeout=300,
        )

        # 페이지별 텍스트도 뽑아 둡니다. 44쪽짜리 정관에서 "별표 6-1"이 몇 쪽인지
        # 모르면 앞 몇 장만 보내게 되고, 그러면 비전이 볼 표가 아예 안 들어갑니다.
        page_texts: dict[str, str] = {}
        pages = sorted(out_dir.glob("page*.png"))
        for index in range(1, len(pages) + 1):
            try:
                result = subprocess.run(
                    ["pdftotext", "-f", str(index), "-l", str(index), str(pdf), "-"],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                page_texts[str(index)] = result.stdout.decode("utf-8", errors="replace")[:4000]
            except Exception:
                page_texts[str(index)] = ""

    (out_dir / "page_text.json").write_text(
        json.dumps(page_texts, ensure_ascii=False), encoding="utf-8"
    )
    return pages, page_texts


def build_table_store(
    docs: list[Document],
    render_structure: bool = True,
    render_raster: bool = False,
    style: str = RENDER_STYLE,
) -> dict[str, Any]:
    """문서별 표 레코드와 이미지를 만듭니다."""

    store: dict[str, Any] = {"documents": {}, "stats": {}}
    total = empty = rendered = rastered = 0

    for doc in docs:
        entries = []
        for table in doc.tables:
            total += 1
            table_id = str(table.get("table_id") or f"{doc.doc_id}-T?")
            empty_flag = is_value_empty(table)
            if empty_flag:
                empty += 1

            image_path: Optional[str] = None
            if render_structure and (table.get("rows") or []):
                safe = unicodedata.normalize("NFC", table_id).replace("/", "_")
                target = TABLE_IMAGE_DIR / (doc.doc_id or "nodoc") / f"{safe}.png"
                try:
                    render_table_png(table, target, style=style)
                    image_path = str(target.relative_to(EVAL_DIR))
                    rendered += 1
                except Exception as exc:  # 렌더 실패는 치명적이지 않습니다
                    print(f"[표렌더 실패] {table_id}: {exc}")

            entries.append(
                {
                    "table_id": table_id,
                    "rows": len(table.get("rows") or []),
                    "nonempty_cells": table.get("nonempty_cells"),
                    "value_empty": empty_flag,
                    "structure_image": image_path,
                }
            )

        raster_paths: list[str] = []
        raster_texts: dict[str, str] = {}
        if render_raster and any(e["value_empty"] for e in entries):
            hwp = next(
                (p for p in REGULATION_DIR.iterdir()
                 if p.suffix.lower() == ".hwp"
                 and unicodedata.normalize("NFC", p.name) == doc.file_name),
                None,
            )
            if hwp is not None:
                try:
                    pages, page_texts = render_page_rasters(hwp, RASTER_DIR / (doc.doc_id or "nodoc"))
                    raster_paths = [str(p.relative_to(EVAL_DIR)) for p in pages]
                    raster_texts = page_texts
                    rastered += 1
                except Exception as exc:
                    print(f"[래스터 실패] {doc.file_name}: {exc}")

        if entries:
            store["documents"][doc.doc_id or doc.file_name] = {
                "file_name": doc.file_name,
                "tables": entries,
                "page_rasters": raster_paths,
                "page_text": raster_texts,
            }

    store["stats"] = {
        "documents_with_tables": len(store["documents"]),
        "total_tables": total,
        "value_empty_tables": empty,
        "structure_images": rendered,
        "documents_rastered": rastered,
    }
    return store


def main() -> None:
    parser = argparse.ArgumentParser(description="표 레인 적재")
    parser.add_argument("--no-structure", action="store_true", help="구조 렌더 생략")
    parser.add_argument("--raster", action="store_true", help="원본 페이지 래스터까지 생성 (느림)")
    parser.add_argument(
        "--style", default=RENDER_STYLE, choices=("compact", "wrapped"),
        help="표 렌더 방식. compact가 더 정확합니다(상수 주석의 측정치 참고).",
    )
    parser.add_argument("--eval8", action="store_true")
    parser.add_argument("--out", default=str(EVAL_DIR / "table_store.json"))
    args = parser.parse_args()

    from eval.corpus import EVAL8_DOC_IDS

    docs = load_documents(doc_ids=EVAL8_DOC_IDS if args.eval8 else None, with_tables=True)
    store = build_table_store(
        docs,
        render_structure=not args.no_structure,
        render_raster=args.raster,
        style=args.style,
    )
    Path(args.out).write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(store["stats"], ensure_ascii=False, indent=2))
    print(f"[표스토어] {args.out} 저장")


if __name__ == "__main__":
    main()

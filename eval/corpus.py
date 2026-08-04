"""규정집 코퍼스 로더.

resources/regulations/ 아래 181개 HWP를 문서 단위로 읽어 옵니다.

두 가지 성가신 점을 여기서 흡수합니다.

1. **파일명이 NFD다.** macOS에서 옮겨 온 저장소라 파일명 한글이 자소 분리(NFD)되어 있습니다.
   181/227개가 NFD입니다. 파이썬 리터럴이나 셸에서 친 NFC 문자열과는 매칭되지 않으므로
   모든 이름을 NFC로 정규화해서 다룹니다.

2. **.cache에 같은 문서가 두 벌 있다.** hwp5txt 캐시 키가 원본 파일명이라
   (app/utils/hwp_extractor.py의 _cache_file) NFD/NFC 이름이 각각 캐시를 만들어
   227개 파일 = 181개 고유 문서가 되어 있습니다. NFC 이름 기준으로 중복을 걷어냅니다.

본문은 .cache의 hwp5txt 결과를 우선 쓰고, 없으면 hwp_extractor로 추출합니다.
(이 개발 머신에는 pyhwp가 없어 캐시 경로만 동작합니다. H200에서는 둘 다 됩니다.)
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
REGULATION_DIR = REPO_ROOT / "resources" / "regulations"
CACHE_DIR = REGULATION_DIR / ".cache"

# rag-test가 평가에 쓴 8문서. 1-0-5는 코퍼스에 없습니다.
EVAL8_DOC_IDS = ("1-0-1", "1-0-2", "1-0-3", "1-0-4", "1-0-6", "1-0-7", "1-0-8", "1-0-9")

# hwp5txt는 표를 이 자리표시자로만 남깁니다. 표 내용을 물으면 구조적으로 답할 수 없다는 표식.
TABLE_PLACEHOLDER = "<표>"

# 변경추적(revision tracking) 상태로 저장된 HWP는 pyhwp가 본문 대신 이 경고문만 뱉습니다.
BROKEN_MARKER = "이 문서는 상위 버전의 변경추적 문서입니다"

DOCUMENT_NUMBER_RE = re.compile(r"^(\d+-\d+-\d+)")


@dataclass
class Document:
    """규정 문서 하나."""

    file_name: str  # NFC 정규화된 HWP 파일명
    doc_id: Optional[str]  # "3-1-10" 같은 문서번호. 없는 문서가 4개 있습니다.
    text: str  # hwp5txt 본문
    tables: list[dict[str, Any]] = field(default_factory=list)  # hwp5html 표 (서버에서만 채워짐)

    @property
    def is_broken(self) -> bool:
        """본문 추출이 실패한 문서인지."""
        return BROKEN_MARKER in self.text[:400]

    @property
    def table_placeholder_count(self) -> int:
        return self.text.count(TABLE_PLACEHOLDER)


def normalize_name(name: str) -> str:
    """NFD 파일명을 NFC로 맞춥니다."""
    return unicodedata.normalize("NFC", name)


def parse_doc_id(file_name: str) -> Optional[str]:
    m = DOCUMENT_NUMBER_RE.match(normalize_name(file_name))
    return m.group(1) if m else None


def _cache_path_for(hwp_name: str) -> Optional[Path]:
    """HWP 이름에 대응하는 캐시 txt를 찾습니다. NFD/NFC 어느 쪽으로 저장돼 있어도 찾습니다."""
    if not CACHE_DIR.is_dir():
        return None
    target = normalize_name(hwp_name)
    if target.lower().endswith(".hwp"):
        target = target[:-4]
    for candidate in CACHE_DIR.iterdir():
        if candidate.suffix != ".txt":
            continue
        if normalize_name(candidate.stem) == target:
            return candidate
    return None


def _iter_hwp_names() -> Iterator[str]:
    """resources/regulations의 HWP 파일명을 NFC 중복 없이 돌려줍니다."""
    seen: set[str] = set()
    for path in sorted(REGULATION_DIR.iterdir()):
        if path.suffix.lower() != ".hwp":
            continue
        if path.name.startswith("._"):  # macOS AppleDouble
            continue
        nfc = normalize_name(path.name)
        if nfc in seen:
            continue
        seen.add(nfc)
        yield path.name  # 실제 on-disk 이름(NFD일 수 있음)을 돌려줘야 파일을 열 수 있습니다


def load_documents(
    doc_ids: Optional[tuple[str, ...]] = None,
    with_tables: bool = False,
) -> list[Document]:
    """규정 문서를 읽어 옵니다.

    Args:
        doc_ids: 지정하면 해당 문서번호만 (8문서 대조 실험용).
        with_tables: True면 hwp5html로 표까지 추출합니다. pyhwp + lxml이 필요하므로
            이 개발 머신에서는 False로만 쓸 수 있습니다.

    Returns:
        list[Document]: 파일명 순 정렬
    """

    docs: list[Document] = []
    for on_disk_name in _iter_hwp_names():
        nfc_name = normalize_name(on_disk_name)
        doc_id = parse_doc_id(nfc_name)
        if doc_ids is not None and doc_id not in doc_ids:
            continue

        cache = _cache_path_for(on_disk_name)
        if cache is not None:
            text = cache.read_text(encoding="utf-8", errors="replace")
        else:
            text = _extract_text(REGULATION_DIR / on_disk_name)

        tables: list[dict[str, Any]] = []
        if with_tables:
            tables = _extract_tables(REGULATION_DIR / on_disk_name)

        docs.append(Document(file_name=nfc_name, doc_id=doc_id, text=text, tables=tables))

    return docs


def _extract_text(path: Path) -> str:
    """pyhwp가 있는 환경에서만 동작합니다."""
    from app.utils.hwp_extractor import extract_text  # 지연 import (pyhwp 의존)

    return extract_text(str(path))


TABLE_CACHE_DIR = REGULATION_DIR / ".tablecache"


def _extract_tables(path: Path) -> list[dict[str, Any]]:
    """pyhwp + lxml이 있는 환경에서만 동작합니다.

    hwp5html은 문서마다 subprocess를 띄우고 181개를 도는 데 몇 분씩 걸립니다.
    인덱스를 여러 벌 빌드하므로 결과를 캐시합니다. (본문은 hwp_extractor가 이미 캐시합니다)
    """

    TABLE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = TABLE_CACHE_DIR / (normalize_name(path.stem) + ".json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    from app.utils.hwp_extractor import extract_tables  # 지연 import

    tables = extract_tables(str(path))
    cache.write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
    return tables


def defect_report(docs: list[Document]) -> dict[str, Any]:
    """코퍼스 결함을 집계합니다. 정확도 수치를 해석할 때 같이 봐야 하는 값들입니다."""

    broken = [d.file_name for d in docs if d.is_broken]
    no_doc_id = [d.file_name for d in docs if not d.doc_id]
    with_placeholder = [(d.file_name, d.table_placeholder_count) for d in docs if d.table_placeholder_count]

    # NFD/NFC 중복은 캐시 디렉터리에서만 생깁니다.
    cache_files = [p for p in CACHE_DIR.iterdir() if p.suffix == ".txt"] if CACHE_DIR.is_dir() else []
    unique_cache = {normalize_name(p.stem) for p in cache_files}

    return {
        "document_count": len(docs),
        "broken_documents": broken,
        "documents_without_doc_id": no_doc_id,
        "table_placeholder_documents": len(with_placeholder),
        "table_placeholder_total": sum(n for _, n in with_placeholder),
        "cache_files": len(cache_files),
        "cache_unique_documents": len(unique_cache),
        "cache_duplicate_pairs": len(cache_files) - len(unique_cache),
        "total_chars": sum(len(d.text) for d in docs),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="코퍼스 로드 및 결함 리포트")
    parser.add_argument("--report", action="store_true", help="결함 리포트 출력")
    parser.add_argument("--eval8", action="store_true", help="1-0-x 8문서만")
    args = parser.parse_args()

    docs = load_documents(doc_ids=EVAL8_DOC_IDS if args.eval8 else None)

    if args.report:
        report = defect_report(docs)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for d in docs:
            print(f"{d.doc_id or '-':<10} {len(d.text):>7}자  {d.file_name}")


if __name__ == "__main__":
    main()

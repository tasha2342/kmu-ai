"""문서에서 날짜를 **역할별로 분리해** 구조화된 사실로 뽑아냅니다.

## 왜 필요한가

현재 청커는 청크마다 `본문날짜:2018-12-03,2021-09-27,...` 형태의 prefix를 붙입니다
(app/utils/regulation_chunker.py의 _build_prefix). 이건 **역할 구분이 없는 평평한 나열**입니다.
그 날짜가 파일명 시행일인지, 부칙 시행일인지, 조항 개정일인지 알 수 없습니다.

8개 정관 문서에서는 "파일명 시행일이 본문에 아예 없다"가 문제였지만(리포트 6.1절),
181문서에서는 178개 문서에서 파일명 시행일이 본문에도 존재합니다. 문제가 *부재*에서
**"평균 14개(최대 196개) 후보 중 어느 것이 정답인가"** 로 바뀝니다.
평평한 나열로는 못 고릅니다. 리포트의 생성 실패 D011/D019/D020이 전부 이 혼동입니다.

## 뽑는 것

| 필드 | 의미 | 근거 |
| --- | --- | --- |
| `filename_effective_date` | 현행 시행일 | 파일명 `(시행2020.12.15.)` |
| `addenda[]` | 부칙별 시행일 + 등장 순번 | `부칙` 블록 헤더와 그 다음 시행 구문 |
| `latest_addendum` | 최신 부칙 | **날짜 크기가 아니라 등장 순번**으로 판정 |
| `first_enacted` | 최초 제정 시행일 | 첫 번째 부칙 |
| `article_revisions` | 조항별 개정일 | 조문 안의 `(개정 …)` `(신설 …)` `(전문개정 …)` |

`latest_addendum`을 날짜 최대값이 아니라 등장 순번으로 정하는 이유: 부칙에는 소급 적용
조항("이 규정은 2019년 3월 1일부터 적용한다")이 섞여 있어 날짜 크기로 고르면 최신 부칙보다
앞선 날짜를 최신으로 잘못 뽑습니다. 규정집은 부칙을 시간 순으로 덧붙이므로 순서가 더 믿을 만합니다.

## 쓰는 곳

1. 청크 prefix를 타입 있는 형태로 바꿀 때 (`typed_prefix_fields`)
2. 날짜 질의에 결정론적으로 답할 때 (`answer_date_question`)

LLM은 쓰지 않습니다. 전부 정규식과 규칙입니다.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from app.utils.regulation_chunker import (
    ADDENDUM_HEAD_RE,
    ARTICLE_HEAD_RE,
    ATTACHMENT_HEAD_RE,
    CHAPTER_HEAD_RE,
    SECTION_SPLIT_RE,
    _section_meta,
    _to_iso_date,
    extract_dates,
    parse_document_number,
    parse_effective_date,
)

from eval.corpus import EVAL8_DOC_IDS, Document, load_documents


# 부칙 본문의 시행 선언. "이 규정은 2020년 12월 15일부터 시행한다"
ENFORCE_CLAUSE_RE = re.compile(r"시행(?:한다|하다|된다|함)")

# 부칙 블록 안의 항목 경계.
#
# 부칙은 코퍼스에서 두 가지 형태로 나옵니다.
#
#   (A) 한 블록에 개정 이력이 번호 목록으로 누적된 형태 — 3-1-10 시설관리규정
#       부칙
#       1. 의료원 시설관리에 관한 규정은 따로 정한다.
#       2. 이 규정은 1982년 10월 11일부터 시행한다.
#       ...
#       8. 이 개정 규정은 2020년 12월 15일부터 시행한다.
#
#   (B) 개정마다 별도 블록이고 헤더 괄호에 날짜가 있는 형태 — 1-0-4 보수규칙(부칙 57개)
#       부 칙(1987.  2.  9.)
#       ①(시행일) 이 규칙은 공포한 날로부터 시행한다.
#
# (A)를 블록 하나로 세면 최신 부칙이 1982년으로 나옵니다(실제로 처음 구현이 그랬고
# 파일명 시행일과의 일치율이 16.8%까지 떨어졌습니다). 항목 단위로 쪼개야 합니다.
ADDENDUM_ITEM_RE = re.compile(r"(?=^\s*\d+[.．]\s)|(?=^\s*[①②③④⑤⑥⑦⑧⑨⑩])", re.MULTILINE)

# 부칙 블록 뒤에 별지 서식이 붙어 오는 경우가 있습니다. 서식은 부칙이 아니므로 잘라냅니다.
ADDENDUM_TAIL_RE = re.compile(r"^\s*\[별[지표]", re.MULTILINE)

# 조문 안의 개정 표기. `(개정 2018. 12. 3., 2021. 9. 27.)` 처럼 날짜가 여러 개 들어갑니다.
#
# `변경`을 빼면 안 됩니다. 정관(1-0-1)은 개정 대신 `(변경 2006. 9. 1.)` 표기를 쓰고,
# 골드 D017/D018이 정확히 그 표기를 묻습니다.
REVISION_MARK_RE = re.compile(
    r"[（(]\s*(?P<kind>개정|변경|신설|전문개정|일부개정|폐지|본조신설|제목개정)"
    r"\s*(?P<body>[^)）]*)[)）]"
)

# 소급 적용 문구. 이게 있는 날짜는 시행일 후보에서 낮게 봅니다.
RETROACTIVE_RE = re.compile(r"적용한다|소급")


@dataclass
class Addendum:
    """부칙 하나."""

    order: int  # 문서 안 등장 순번 (1부터)
    header: str  # "부칙(2020. 12. 15.)" 같은 첫 줄
    header_date: Optional[str]  # 헤더 괄호 안 날짜
    enforced_from: Optional[str]  # 본문 시행 구문에서 뽑은 날짜
    retroactive: bool  # 소급 적용 문구 포함 여부
    text: str = ""

    @property
    def effective(self) -> Optional[str]:
        """이 부칙의 대표 시행일. 시행 구문 > 헤더 날짜 순으로 신뢰합니다."""
        return self.enforced_from or self.header_date


@dataclass
class DateFacts:
    """문서 하나의 날짜 사실 전부."""

    doc_id: Optional[str]
    file_name: str
    filename_effective_date: Optional[str]
    addenda: list[Addendum] = field(default_factory=list)
    article_revisions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def latest_addendum(self) -> Optional[Addendum]:
        """가장 나중에 등장한 부칙. 날짜 크기가 아니라 순번 기준입니다."""
        return self.addenda[-1] if self.addenda else None

    @property
    def first_enacted(self) -> Optional[str]:
        """최초 제정 시행일. 첫 부칙의 시행일입니다."""
        return self.addenda[0].effective if self.addenda else None

    @property
    def latest_effective(self) -> Optional[str]:
        latest = self.latest_addendum
        return latest.effective if latest else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "file_name": self.file_name,
            "filename_effective_date": self.filename_effective_date,
            "first_enacted": self.first_enacted,
            "latest_addendum": asdict(self.latest_addendum) if self.latest_addendum else None,
            "addenda": [asdict(a) for a in self.addenda],
            "article_revisions": self.article_revisions,
        }


def _split_blocks(text: str) -> list[str]:
    """chunk_regulation과 동일한 블록 분할.

    같은 경계를 써야 날짜 팩트와 청크가 어긋나지 않습니다.
    """

    normalized = (text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []

    blocks: list[str] = []
    buffer = ""
    for part in SECTION_SPLIT_RE.split(normalized):
        if not part or not part.strip():
            continue
        head = part.strip()
        is_head = bool(
            ARTICLE_HEAD_RE.match(head)
            or ATTACHMENT_HEAD_RE.match(head)
            or ADDENDUM_HEAD_RE.match(head)
            or CHAPTER_HEAD_RE.match(head)
        )
        if is_head:
            if buffer.strip():
                blocks.append(buffer.strip())
            buffer = part
        else:
            buffer += part
    if buffer.strip():
        blocks.append(buffer.strip())
    return blocks


def _enforced_date(text: str) -> Optional[str]:
    """'…부터 시행한다' 구문에 딸린 날짜를 고릅니다.

    부칙에는 시행일·적용일·경과조치일이 섞여 있습니다. 시행 선언이 있는 **줄**의
    날짜만 취합니다. 없으면 None — 아무 날짜나 집어오지 않습니다.
    """

    for line in text.splitlines():
        if not ENFORCE_CLAUSE_RE.search(line):
            continue
        dates = extract_dates(line)
        if dates:
            return dates[0]
    return None


def _header_date(first_line: str) -> Optional[str]:
    """`부칙(2020. 12. 15.)` 헤더 괄호 안 날짜."""
    dates = extract_dates(first_line)
    return dates[0] if dates else None


def _addenda_from_block(block: str, start_order: int) -> list[Addendum]:
    """부칙 블록 하나에서 부칙 항목들을 뽑습니다. (A)형은 여러 개, (B)형은 하나.

    판별 규칙은 형태가 아니라 내용입니다 — **시행 선언과 날짜를 함께 가진 항목**만
    독립된 부칙으로 셉니다. 이러면 (A)형 `2. 이 규정은 1982년…부터 시행한다`는 잡히고,
    (B)형의 `②(경과조치) …` 같은 항은 잡히지 않습니다. 학칙(2-0-1)처럼 한 부칙 안에
    `① 시행 / ② 경과조치 / ③ 경과조치`가 들어 있는 경우도 ①만 부칙이 됩니다.

    Args:
        block: 부칙 블록 원문
        start_order: 이 블록의 첫 항목에 부여할 순번

    Returns:
        list[Addendum]: 문서 등장 순서대로
    """

    trimmed = block
    tail = ADDENDUM_TAIL_RE.search(block)
    if tail:
        trimmed = block[: tail.start()]

    lines = trimmed.strip().splitlines()
    first_line = lines[0].strip() if lines else ""
    header_date = _header_date(first_line)
    body = "\n".join(lines[1:])

    items = [p for p in ADDENDUM_ITEM_RE.split(body) if p.strip()]
    out: list[Addendum] = []
    order = start_order

    for item in items:
        enforced = _enforced_date(item)
        if not enforced:
            continue
        out.append(
            Addendum(
                order=order,
                header=first_line,
                header_date=header_date,
                enforced_from=enforced,
                retroactive=bool(RETROACTIVE_RE.search(item)),
                text=item.strip()[:300],
            )
        )
        order += 1

    if out:
        return out

    # 시행 날짜를 가진 항목이 하나도 없는 블록. "공포한 날로부터 시행한다"처럼
    # 본문에 날짜가 없는 (B)형이 여기 옵니다. 헤더 날짜로 대표시킵니다.
    if header_date or extract_dates(trimmed):
        return [
            Addendum(
                order=start_order,
                header=first_line,
                header_date=header_date,
                enforced_from=None,
                retroactive=bool(RETROACTIVE_RE.search(trimmed)),
                text=trimmed.strip()[:300],
            )
        ]

    return []


def _revision_dates(body: str) -> list[str]:
    """`(개정 2018. 12. 3., 2021. 9. 27.)` 안의 날짜들을 ISO로."""
    out: list[str] = []
    for m in re.finditer(r"(19\d{2}|20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})", body):
        iso = _to_iso_date(m.group(1), m.group(2), m.group(3))
        if iso:
            out.append(iso)
    return out


def extract_date_facts(doc: Document) -> DateFacts:
    """문서 하나에서 날짜 사실을 뽑습니다."""

    facts = DateFacts(
        doc_id=doc.doc_id or parse_document_number(doc.file_name),
        file_name=doc.file_name,
        filename_effective_date=parse_effective_date(doc.file_name),
    )

    order = 0
    for block in _split_blocks(doc.text):
        meta = _section_meta(block)
        first_line = block.strip().splitlines()[0] if block.strip() else ""

        if meta["section_type"] == "addendum":
            found = _addenda_from_block(block, order + 1)
            facts.addenda.extend(found)
            order += len(found)
            continue

        if meta["section_type"] == "article" and meta["article"]:
            dates: list[str] = []
            for m in REVISION_MARK_RE.finditer(block):
                dates.extend(_revision_dates(m.group("body")))
            if dates:
                # 같은 조항이 여러 청크로 쪼개져도 합쳐지도록 누적합니다.
                bucket = facts.article_revisions.setdefault(meta["article"], [])
                for d in dates:
                    if d not in bucket:
                        bucket.append(d)

    for bucket in facts.article_revisions.values():
        bucket.sort()

    return facts


def typed_prefix_fields(facts: DateFacts, chunk: dict[str, Any]) -> list[str]:
    """청크 prefix에 넣을 **타입 있는** 날짜 항목을 만듭니다.

    현재 구현의 `본문날짜:...` 를 대체합니다. 같은 날짜라도 역할이 붙으면
    BM25와 dense 양쪽이 "무슨 날짜인지"를 보게 됩니다.
    """

    bits: list[str] = []
    section_type = chunk.get("section_type")
    article = chunk.get("article")

    if facts.filename_effective_date:
        bits.append(f"파일명시행일:{facts.filename_effective_date}")

    if section_type == "addendum":
        # 어느 부칙인지 청크 본문으로 되짚습니다.
        body = chunk.get("text") or ""
        matched = None
        for a in facts.addenda:
            if a.header and a.header in body:
                matched = a
                break
        if matched is None and facts.addenda:
            matched = facts.latest_addendum
        if matched:
            if matched.effective:
                bits.append(f"부칙시행일:{matched.effective}")
            total = len(facts.addenda)
            suffix = "(최신)" if matched.order == total else ""
            bits.append(f"부칙순번:{matched.order}/{total}{suffix}")
            if matched.retroactive:
                bits.append("소급적용포함:예")
    elif section_type == "article" and article:
        revisions = facts.article_revisions.get(article)
        if revisions:
            bits.append(f"조항개정일:{','.join(revisions[:8])}")
    else:
        dates = extract_dates(chunk.get("text") or "")
        if dates:
            bits.append(f"본문날짜:{','.join(dates[:8])}")

    return bits


# ── 날짜 질의 하위유형 판별 및 결정론적 응답 ──────────────────────────────────

FILENAME_TRIGGERS = ("파일명", "현행시행", "현행")
FIRST_TRIGGERS = ("최초", "제정된", "처음", "맨처음")
LATEST_TRIGGERS = ("최신", "최근", "마지막", "최종")
# 부칙을 가리키는 말. "개정규정/개정학칙/개정내규 시행일"도 부칙 시행일을 묻는 것입니다.
ADDENDUM_TRIGGERS = ("부칙", "개정규정", "개정학칙", "개정내규", "개정규칙", "개정된")
ARTICLE_REVISION_TRIGGERS = ("개정", "변경", "신설", "전문개정")

# 특정 과거 부칙을 한정하는 표현. "2017학년도", "2017년" 처럼 답이 아니라 조건으로 쓰인 연도.
YEAR_QUALIFIER_RE = re.compile(r"((?:19|20)\d{2})\s*(?:학년도|년도|년)")


def classify_date_question(question: str) -> str:
    """날짜 질의의 하위유형.

    골드셋 subtype과의 대응:
        `filename_effective`/`paraphrase_filename` → filename_effective
        `latest_byeolchik`                        → latest_addendum
        `historical_byeolchik`                    → first_enacted 또는 historical_addendum
        `article_revision`                        → article_revision

    판정 순서가 중요합니다. "정관 제2조가 변경된 날짜"는 조항 개정이지 시행일이 아니고,
    "표창 규정이 최초로 시행된 날"은 최초 부칙이지 파일명 시행일이 아닙니다.
    """

    q = question.replace(" ", "")
    has_article = bool(re.search(r"제\d+조", question))

    if has_article and any(t in q for t in ARTICLE_REVISION_TRIGGERS):
        return "article_revision"
    if any(t in q for t in FIRST_TRIGGERS):
        return "first_enacted"
    if any(t in q for t in FILENAME_TRIGGERS):
        return "filename_effective"

    if any(t in q for t in ADDENDUM_TRIGGERS):
        # "부칙 중 2017학년도 …" 처럼 연도가 조건으로 붙으면 특정 과거 부칙을 묻는 것입니다.
        if YEAR_QUALIFIER_RE.search(question):
            return "historical_addendum"
        return "latest_addendum"

    if any(t in q for t in LATEST_TRIGGERS):
        return "latest_addendum"
    if "시행일" in q or "시행된" in q:
        return "filename_effective"
    return "unknown"


def _historical_addendum(question: str, facts: DateFacts) -> Optional[str]:
    """질의에 박힌 연도로 특정 과거 부칙을 집어냅니다."""

    m = YEAR_QUALIFIER_RE.search(question)
    if not m:
        return None
    year = m.group(1)

    # 부칙 본문에 그 연도가 언급된 것을 우선 찾고, 없으면 시행일 연도가 맞는 것을 찾습니다.
    for addendum in facts.addenda:
        if year in (addendum.text or ""):
            return addendum.effective
    for addendum in facts.addenda:
        if (addendum.effective or "").startswith(year):
            return addendum.effective
    return None


def answer_date_question(question: str, facts: DateFacts) -> Optional[str]:
    """날짜 질의에 팩트 스토어에서 직접 답합니다.

    검색은 **어느 문서인지**만 맞히면 되고, 날짜 값은 여기서 결정론적으로 나옵니다.
    평균 14개(최대 196개) 후보 중 고르는 문제가 1회 조회로 바뀝니다.
    """

    kind = classify_date_question(question)

    if kind == "filename_effective":
        return facts.filename_effective_date
    if kind == "latest_addendum":
        return facts.latest_effective
    if kind == "first_enacted":
        return facts.first_enacted
    if kind == "historical_addendum":
        return _historical_addendum(question, facts) or facts.latest_effective
    if kind == "article_revision":
        m = re.search(r"제\d+조(?:의\d+)?", question)
        if m:
            revisions = facts.article_revisions.get(m.group(0))
            if revisions:
                return revisions[-1]
        return None
    return facts.filename_effective_date


def build_fact_store(docs: list[Document]) -> dict[str, DateFacts]:
    """문서번호 → 날짜 팩트. 번호 없는 문서는 파일명을 키로 씁니다."""
    store: dict[str, DateFacts] = {}
    for doc in docs:
        facts = extract_date_facts(doc)
        store[facts.doc_id or facts.file_name] = facts
    return store


def selftest(docs: list[Document]) -> dict[str, Any]:
    """추출기 자기 검증.

    핵심 교차 검증: 규정집은 최신 부칙 시행일과 파일명 시행일이 같은 것이 정상입니다.
    (파일명의 `(시행YYYY.M.D.)`가 최신 개정의 시행일이므로.) 둘이 어긋나는 문서 비율이
    높으면 부칙 순번-날짜 짝짓기가 틀린 것입니다.
    """

    total = len(docs)
    no_filename_date = 0
    no_addendum = 0
    agree = 0
    disagree: list[dict[str, Any]] = []
    addenda_counts: list[int] = []
    revision_docs = 0

    for doc in docs:
        facts = extract_date_facts(doc)
        addenda_counts.append(len(facts.addenda))
        if facts.article_revisions:
            revision_docs += 1
        if not facts.filename_effective_date:
            no_filename_date += 1
            continue
        if not facts.addenda:
            no_addendum += 1
            continue
        if facts.latest_effective == facts.filename_effective_date:
            agree += 1
        else:
            disagree.append(
                {
                    "doc_id": facts.doc_id,
                    "file_name": facts.file_name,
                    "filename": facts.filename_effective_date,
                    "latest_addendum": facts.latest_effective,
                    "addenda": len(facts.addenda),
                }
            )

    comparable = total - no_filename_date - no_addendum
    return {
        "documents": total,
        "filename_date_parse_failures": no_filename_date,
        "documents_without_addendum": no_addendum,
        "comparable": comparable,
        "latest_addendum_matches_filename": agree,
        "agreement_rate": round(agree / comparable, 4) if comparable else None,
        "addenda_per_doc_mean": round(statistics.mean(addenda_counts), 2) if addenda_counts else 0,
        "addenda_per_doc_max": max(addenda_counts) if addenda_counts else 0,
        "documents_with_article_revisions": revision_docs,
        "disagreements_sample": disagree[:10],
        "disagreement_count": len(disagree),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="날짜 팩트 추출")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--eval8", action="store_true")
    parser.add_argument("--dump", metavar="DOC_ID", help="문서 하나의 팩트를 출력")
    parser.add_argument("--out", metavar="PATH", help="전체 팩트를 JSON으로 저장")
    args = parser.parse_args()

    docs = load_documents(doc_ids=EVAL8_DOC_IDS if args.eval8 else None)

    if args.dump:
        for doc in docs:
            if doc.doc_id == args.dump:
                print(json.dumps(extract_date_facts(doc).to_dict(), ensure_ascii=False, indent=2))
                return
        print(f"문서를 찾을 수 없습니다: {args.dump}")
        return

    if args.out:
        store = build_fact_store(docs)
        payload = {k: v.to_dict() for k, v in store.items()}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"{len(payload)}개 문서의 날짜 팩트를 {args.out}에 저장했습니다.")
        return

    print(json.dumps(selftest(docs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

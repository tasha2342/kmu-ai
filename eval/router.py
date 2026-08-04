"""질의를 날짜/표/일반 레인으로 나눕니다.

리포트가 보여준 건 "유형마다 정확도를 올린 수단이 달랐다"는 것입니다. 날짜는 메타 청크와
날짜 prefix로(33%→95.8%), 표는 hwp5html 별도 직렬화로(45.8%→87.5%), 빈 셀 표는 비전으로(→100%).
그런데 지금 구현은 질의가 들어와도 유형을 보지 않고 한 풀에서 검색합니다.
표 질문이 본문 청크와, 날짜 질문이 부칙 청크와 같이 경쟁합니다.

여기서 유형을 판별해 레인을 고릅니다. 규칙 기반이고 LLM을 쓰지 않습니다 —
라우팅에 LLM 한 콜을 더 쓰면 지연이 늘고, 규칙만으로 골드셋에서 충분히 맞습니다.

라우터가 틀렸을 때를 대비해 호출부는 일반 레인을 **항상 병렬로** 돌리고 결과를 합칩니다.
라우팅이 실패해도 현재 수준 아래로 떨어지지 않게 하려는 장치입니다.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Optional

from eval.date_facts import classify_date_question


# 표 질의 신호. 별표/표 자체를 가리키는 말과, 표에만 들어 있는 값의 이름들.
TABLE_KEYWORDS = (
    "별표",
    "별지",
    "표에",
    "표의",
    "도표",
    "정원",
    "수당",
    "호봉",
    "봉급",
    "급여",
    "지급률",
    "지급 비율",
    "감액",
    "산정",
    "금액",
    "얼마",
    "몇 명",
    "몇명",
    "인원",
    "등급",
    "구분표",
)

# 날짜 질의 신호.
DATE_KEYWORDS = (
    "시행일",
    "시행 일",
    "시행된",
    "개정",
    "제정",
    "부칙",
    "언제",
    "몇 년",
    "몇년",
    "날짜",
    "일자",
)

# 표 안의 값을 묻는지, 표의 메타(제목·행 수)를 묻는지.
TABLE_META_KEYWORDS = ("몇 개", "몇개", "제목", "항목 수", "행 수", "구성")

ARTICLE_RE = re.compile(r"제\d+조(?:의\d+)?")
DOC_ID_RE = re.compile(r"\d+-\d+-\d+")


@dataclass
class Route:
    """라우팅 결과."""

    lane: str  # "date" | "table" | "general"
    subtype: Optional[str]  # 날짜 레인일 때의 하위유형
    doc_id: Optional[str]  # 질의에 문서번호가 박혀 있으면
    article: Optional[str]  # 질의에 조항이 박혀 있으면

    @property
    def section_filter(self) -> Optional[dict[str, str]]:
        """검색 시 넘길 filter_conditions.

        vector_store._apply_filter_conditions()가 이미 지원하는데 규정 검색 경로가
        한 번도 안 넘기던 값입니다. 표 질의를 표 청크로 좁히는 데 씁니다.
        """
        if self.lane == "table":
            return {"section_type": "table"}
        return None


def _score(question: str, keywords: tuple[str, ...]) -> int:
    q = question.replace(" ", "")
    return sum(1 for k in keywords if k.replace(" ", "") in q)


def route(question: str) -> Route:
    """질의 하나를 레인에 배정합니다."""

    doc_id_match = DOC_ID_RE.search(question)
    article_match = ARTICLE_RE.search(question)

    table_score = _score(question, TABLE_KEYWORDS)
    date_score = _score(question, DATE_KEYWORDS)

    # 날짜 신호가 있으면 날짜가 우선입니다. "별표 6-1의 시행일"처럼 둘 다 걸리는 질의는
    # 답이 날짜이므로 날짜 레인이 맞습니다.
    if date_score > 0 and date_score >= table_score:
        return Route(
            lane="date",
            subtype=classify_date_question(question),
            doc_id=doc_id_match.group(0) if doc_id_match else None,
            article=article_match.group(0) if article_match else None,
        )

    if table_score > 0:
        return Route(
            lane="table",
            subtype="table_meta" if _score(question, TABLE_META_KEYWORDS) else "cell_value",
            doc_id=doc_id_match.group(0) if doc_id_match else None,
            article=article_match.group(0) if article_match else None,
        )

    return Route(
        lane="general",
        subtype=None,
        doc_id=doc_id_match.group(0) if doc_id_match else None,
        article=article_match.group(0) if article_match else None,
    )

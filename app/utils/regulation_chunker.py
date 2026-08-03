"""학칙·규정 문서 전용 청킹 모듈입니다.

`app/utils/text_chunker.py`의 범용 청커와 **분리된 별도 모듈**입니다.
규정 특화 규칙(조/부칙/별표 경계, 문서메타 청크, 표 Markdown 청크)을 범용 청커에 섞으면
일반 문서 업로드 경로가 함께 망가지므로 절대 합치지 마세요.

전략의 근거는 `rag-test/docs/rag_test_report.md`의 측정 결과입니다.
계명대 정관·규정 8개 문서 / 골드셋 48문항(날짜 24 + 표 24) 기준:

| 단계 | 전체 | 날짜 | 표 |
|---|---|---|---|
| E0 고정 800자 청킹 | 39.6% | 33.3% | 45.8% |
| E1 조항 인식 청킹 | 41.7% | 33.3% | 50.0% |
| E2 + 문서메타/날짜 prefix | 62.5% | 79.2% | 45.8% |
| E3 + 표 Markdown 청크 | 83.3% | 87.5% | 79.2% |
| E4 hybrid top-k=12 | 91.7% | 95.8% | 87.5% |

즉 정확도를 두 배로 끌어올린 결정적 요인은 **문서메타 청크(E2)**와 **표 청크(E3)**입니다.
이 두 가지를 걷어내면 날짜 정확도는 79% → 33%, 표 정확도는 79% → 46%로 되돌아갑니다.
"""

import re
import unicodedata

from datetime import date
from typing import Any, Optional


# 조/부칙/별표/장/절 경계에서 자릅니다. 고정 길이 청킹(E0)은 조문 중간을 잘라
# 근거가 두 청크에 흩어지는 chunking_failure를 만들었습니다(리포트 3절).
SECTION_SPLIT_RE = re.compile(
    r"(?=^제\d+조(?:의\d+)?\s*[\(（])"
    r"|(?=^부\s*칙)"
    r"|(?=^\[별표[^\]]*\])"
    r"|(?=^제\d+장\b)"
    r"|(?=^제\d+절\b)",
    re.MULTILINE,
)

ARTICLE_HEAD_RE = re.compile(r"^제(?P<number>\d+)조(?:의(?P<sub>\d+))?")
ATTACHMENT_HEAD_RE = re.compile(r"^\[별표[^\]]*\]")
ADDENDUM_HEAD_RE = re.compile(r"^부\s*칙")
CHAPTER_HEAD_RE = re.compile(r"^제\d+[장절]")

# 본문 안의 날짜. `2018. 7. 1.` `2018-07-01` `2018년 7월 1일` 형태를 모두 받습니다.
DATE_IN_TEXT_RE = re.compile(
    r"(19\d{2}|20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?"
)

# 파일명 시행일. 코퍼스 파일명은 `(시행2020.12.15.)`처럼 붙어 있기도 하고
# `(시행+2018.+7.+1)`처럼 공백이 전부 `+`로 치환돼 있기도 해서 구분자를 넓게 허용합니다.
#
# 연·월·일 각각에 `(?!\d)` 경계를 둔 이유: 코퍼스에 `(시행+20108.+7.+9.)`처럼
# 연도가 5자리인 오타 파일명이 있습니다. 경계가 없으면 `2010`년 `8`월 `7`일이라는
# 그럴듯하지만 틀린 날짜를 만들어 냅니다. 날짜는 RAG 답변에 그대로 인용되므로
# 틀린 값을 지어내느니 None을 반환하는 편이 안전합니다.
FILENAME_DATE_RE = re.compile(
    r"시행\s*\+?\s*(\d{4})(?!\d)"
    r"\s*[.\-년]?\s*\+?\s*(\d{1,2})(?!\d)"
    r"\s*[.\-월]?\s*\+?\s*(\d{1,2})(?!\d)"
)

# 문서번호. rag-test 원본은 `1-0-\d+`로 하드코딩돼 있었으나, 실제 코퍼스에는
# `1-0-*`, `2-0-*`, `3-1-*` ~ `3-4-*`, `5-1-*`가 섞여 있고 번호 없는 파일명도 있습니다.
DOCUMENT_NUMBER_RE = re.compile(r"^(\d+-\d+-\d+)")

# 청크를 더 쪼갤 때 쓰는 항(項) 경계. 조문은 ①②③ 또는 `1.` `2.`로 항이 나뉩니다.
CLAUSE_MARKER_RE = re.compile(r"(?=^[①②③④⑤⑥⑦⑧⑨⑩]|^\s*[0-9]+[.．)]\s)", re.MULTILINE)

# 표 청크의 빈 셀 경고 문구. 리포트 7절 T024 사례에서 이 문구가
# 모델이 없는 금액을 지어내지 않고 "모른다"고 기권하게 만든 장치입니다. 제거 금지.
EMPTY_TABLE_WARNING = (
    "[주의] 이 표의 수치 셀이 비어 있거나 거의 비어 있습니다. 금액을 추정하지 마세요."
)


def _normalize(file_name: str) -> str:
    """파일명을 NFC로 정규화합니다.

    코퍼스 파일명이 디스크에 NFD(자모 분리)로 저장돼 있어, 정규화하지 않으면
    `제`, `조` 같은 한글 리터럴 비교와 정규식이 전부 빗나갑니다.

    Args:
        file_name (str): 원본 파일명

    Returns:
        str: NFC 정규화된 파일명
    """

    return unicodedata.normalize("NFC", file_name)


def parse_document_number(file_name: str) -> Optional[str]:
    """파일명 앞머리의 문서번호를 파싱합니다.

    `1-0-1학교법인계명대학교정관(시행2026.7.1.).hwp` -> `1-0-1`

    번호 없는 파일명(예: `JA(Joint+Appointment)교원운영내규(...).hwp`)도 코퍼스에 있으며,
    이 경우 None을 반환합니다. 청킹은 문서번호 없이도 정상 동작해야 합니다.

    Args:
        file_name (str): HWP 파일명

    Returns:
        Optional[str]: 문서번호. 번호가 없으면 None
    """

    match = DOCUMENT_NUMBER_RE.match(_normalize(file_name))
    return match.group(1) if match else None


def parse_effective_date(file_name: str) -> Optional[str]:
    """파일명의 시행일을 ISO 문자열로 파싱합니다.

    **파일명 시행일은 본문 어디에도 없습니다.** 고정 길이 청킹(E0)이 날짜 질문에서
    본문의 개정 이력 날짜를 계속 오답으로 골랐던 근본 원인이 이것입니다(리포트 6.1절).
    이 값을 메타데이터로 심어 준 뒤 날짜 정확도가 33% → 79%로 올랐습니다.

    Args:
        file_name (str): HWP 파일명

    Returns:
        Optional[str]: `YYYY-MM-DD` 형식 시행일. 파싱 실패 또는 날짜가 유효하지 않으면 None
    """

    match = FILENAME_DATE_RE.search(_normalize(file_name))
    if not match:
        return None

    return _to_iso_date(match.group(1), match.group(2), match.group(3))


def _to_iso_date(year: str, month: str, day: str) -> Optional[str]:
    """연/월/일 문자열을 검증 후 `YYYY-MM-DD`로 변환합니다.

    Args:
        year (str): 연도
        month (str): 월
        day (str): 일

    Returns:
        Optional[str]: ISO 날짜 문자열. 실제 존재하지 않는 날짜면 None
    """

    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def extract_dates(text: str) -> list[str]:
    """텍스트에서 날짜를 모두 뽑아 ISO 문자열 목록으로 반환합니다.

    청크 메타데이터 `dates_in_chunk`에 담겨, 날짜 질의 시 어떤 날짜 후보가
    이 청크 안에 있는지 검색·재순위 단계에서 활용됩니다.

    Args:
        text (str): 대상 텍스트

    Returns:
        list[str]: `YYYY-MM-DD` 형식 날짜 목록 (등장 순서, 중복 유지)
    """

    dates = []
    for match in DATE_IN_TEXT_RE.finditer(text or ""):
        iso = _to_iso_date(match.group(1), match.group(2), match.group(3))
        if iso:
            dates.append(iso)

    return dates


def _document_title(file_name: str, text: str) -> str:
    """문서 제목을 결정합니다.

    본문 첫 줄이 대체로 문서 제목입니다. 본문이 비었거나 첫 줄이 쓸 만하지 않으면
    확장자를 뗀 파일명을 씁니다.

    Args:
        file_name (str): HWP 파일명
        text (str): 본문 텍스트

    Returns:
        str: 문서 제목
    """

    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:100]

    return re.sub(r"\.hwp$", "", _normalize(file_name), flags=re.IGNORECASE)


def _make_chunk(
    text: str,
    file_name: str,
    doc_id: Optional[str],
    article: Optional[str],
    section_type: str,
    effective_date: Optional[str],
    table_id: Optional[str] = None
) -> dict[str, Any]:
    """청크 dict를 생성합니다.

    Args:
        text (str): 청크 본문
        file_name (str): 출처 파일명
        doc_id (Optional[str]): 문서번호
        article (Optional[str]): 조항 식별자 (예: `제12조`)
        section_type (str): 섹션 유형 (`meta`/`article`/`addendum`/`attachment`/`chapter`/`section`/`table`/`body`)
        effective_date (Optional[str]): 파일명 기준 시행일
        table_id (Optional[str]): 표 ID

    Returns:
        dict[str, Any]: 청크 dict
    """

    return {
        "text": text,
        "source": file_name,
        "doc_id": doc_id,
        "article": article,
        "section_type": section_type,
        "effective_date": effective_date,
        "dates_in_chunk": extract_dates(text)[:20],
        "table_id": table_id,
    }


def build_document_meta_chunk(file_name: str, text: str) -> dict[str, Any]:
    """문서당 1개의 문서메타 청크를 만듭니다. (E2의 핵심)

    파일명 시행일은 본문에 존재하지 않기 때문에, 검색으로는 절대 닿을 수 없는
    정보였습니다. 이 청크가 그 정보를 검색 가능한 텍스트로 만들어 줍니다.

    **이 함수를 제거하면 날짜 질의 정확도가 79% → 33%로 떨어집니다(리포트 5.1절 E1 vs E2).**
    "왜 문서마다 이런 청크가 하나씩 붙지?" 싶더라도 지우지 마세요.

    본문에는 개정 이력 날짜가 수십 개씩 들어 있어, 메타 청크가 없으면 모델이
    그 중 아무 날짜나 현행 시행일로 골라 답합니다.

    Args:
        file_name (str): HWP 파일명
        text (str): 본문 텍스트 (제목 추출에만 사용)

    Returns:
        dict[str, Any]: 문서메타 청크 1개
    """

    doc_id = parse_document_number(file_name)
    effective_date = parse_effective_date(file_name)
    title = _document_title(file_name, text)

    header = (
        f"[문서메타] 문서번호={doc_id or '없음'} "
        f"| 파일명시행일={effective_date or '알수없음'} "
        f"| 제목={title}"
    )

    lines = [header, f"파일명={re.sub(r'\.hwp$', '', _normalize(file_name), flags=re.IGNORECASE)}"]

    # 아래 두 문장은 임베딩 검색이 "시행일이 언제인가" 형태의 질의와 매칭되도록
    # 자연문으로 풀어 쓴 것입니다. 키=값 나열만으로는 유사도가 충분히 오르지 않았습니다.
    if effective_date:
        lines.append(f"이 문서의 현행 파일명 기준 시행일은 {effective_date} 이다.")
        lines.append(
            f"질문 유형이 파일명 시행일 또는 현행 시행 기준일이면 {effective_date} 을 답한다."
        )
    else:
        lines.append("이 문서는 파일명에서 시행일을 확인할 수 없다. 시행일을 추정하지 마세요.")

    return _make_chunk(
        text="\n".join(lines),
        file_name=file_name,
        doc_id=doc_id,
        article="문서메타",
        section_type="meta",
        effective_date=effective_date,
    )


def _section_meta(block: str) -> dict[str, Any]:
    """블록의 첫 줄을 보고 섹션 유형과 조항 식별자를 판별합니다.

    Args:
        block (str): 청킹 대상 블록

    Returns:
        dict[str, Any]: `{"article", "section_type", "table_id"}`
    """

    stripped = block.strip()
    first_line = stripped.splitlines()[0] if stripped else ""

    meta: dict[str, Any] = {"article": None, "section_type": "body", "table_id": None}

    if ADDENDUM_HEAD_RE.match(first_line):
        # 부칙은 조문과 시행일 체계가 달라 반드시 분리합니다. 부칙 시행일과
        # 조항 개정일이 한 청크에 섞이면 모델이 둘을 혼동합니다(리포트 6.1절).
        meta["section_type"] = "addendum"
        meta["article"] = "부칙"
    elif ATTACHMENT_HEAD_RE.match(first_line):
        meta["section_type"] = "attachment"
        meta["article"] = first_line.strip()
        meta["table_id"] = first_line.strip()
    elif ARTICLE_HEAD_RE.match(first_line):
        match = ARTICLE_HEAD_RE.match(first_line)
        number = match.group("number")
        sub = match.group("sub")
        meta["article"] = f"제{number}조의{sub}" if sub else f"제{number}조"
        meta["section_type"] = "article"
    elif first_line.startswith("제") and "장" in first_line[:8]:
        meta["section_type"] = "chapter"
        meta["article"] = first_line.strip()[:20]
    elif first_line.startswith("제") and "절" in first_line[:8]:
        meta["section_type"] = "section"
        meta["article"] = first_line.strip()[:20]

    return meta


def _window_split(text: str, size: int, overlap: int) -> list[str]:
    """텍스트를 고정 길이 창으로 분할합니다.

    조·항 경계를 찾을 수 없을 때만 쓰는 최후 수단입니다.

    Args:
        text (str): 대상 텍스트
        size (int): 창 크기
        overlap (int): 창 간 겹침

    Returns:
        list[str]: 분할된 조각 목록
    """

    pieces = []
    start = 0
    while start < len(text):
        end = start + size
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = end - overlap

    return pieces


def _split_long_block(block: str, max_chars: int) -> list[str]:
    """긴 블록을 항(項) 경계 우선으로 더 쪼갭니다.

    조문 하나가 `max_chars`를 넘어도 무작정 잘라 내면 항 중간이 끊깁니다.
    ①②③ 또는 `1.` `2.` 항 마커가 2개 이상 있으면 그 경계를 씁니다.

    Args:
        block (str): 대상 블록
        max_chars (int): 조각 최대 길이

    Returns:
        list[str]: 분할된 조각 목록
    """

    if len(block) <= max_chars:
        return [block]

    markers = list(CLAUSE_MARKER_RE.finditer(block))
    if len(markers) < 2:
        return _window_split(block, max_chars, overlap=100)

    boundaries = [m.start() for m in markers] + [len(block)]
    parts = []
    for index in range(len(boundaries) - 1):
        end = boundaries[index + 1]
        # 첫 마커 앞의 조문 헤더(제N조(제목))는 첫 조각에 붙여 둡니다.
        # 헤더가 떨어져 나가면 그 조각이 어느 조문인지 검색으로 알 수 없게 됩니다.
        start = 0 if index == 0 else boundaries[index]
        segment = block[start:end].strip()
        if segment:
            parts.append(segment)

    # 항이 짧게 쪼개지면 청크가 과다해져 검색 노이즈가 됩니다. 다시 합칩니다.
    merged: list[str] = []
    for part in parts:
        if merged and len(merged[-1]) + len(part) < max_chars:
            merged[-1] = f"{merged[-1]}\n{part}"
        else:
            merged.append(part)

    pieces: list[str] = []
    for part in merged:
        if len(part) <= max_chars:
            pieces.append(part)
        else:
            pieces.extend(_window_split(part, max_chars, overlap=100))

    return pieces


def _build_prefix(
    doc_id: Optional[str],
    effective_date: Optional[str],
    meta: dict[str, Any],
    piece: str
) -> str:
    """청크 본문 앞에 붙일 메타 prefix를 만듭니다. (E2)

    조각 텍스트만으로는 "몇 번 문서의 몇 조인지"를 알 수 없습니다.
    prefix를 붙이면 임베딩과 BM25 양쪽에서 문서·조항 지정 질의가 걸립니다.

    Args:
        doc_id (Optional[str]): 문서번호
        effective_date (Optional[str]): 파일명 시행일
        meta (dict[str, Any]): 섹션 메타
        piece (str): 청크 본문

    Returns:
        str: `[문서:... | 파일명시행일:... | 조항:...]` 형태의 prefix
    """

    bits = []
    if doc_id:
        bits.append(f"문서:{doc_id}")
    if effective_date:
        bits.append(f"파일명시행일:{effective_date}")
    if meta["article"]:
        bits.append(f"조항:{meta['article']}")
    if meta["section_type"]:
        bits.append(f"유형:{meta['section_type']}")

    dates = extract_dates(piece)
    if dates:
        bits.append(f"본문날짜:{','.join(dates[:8])}")

    return "[" + " | ".join(bits) + "]"


def chunk_regulation(
    text: str,
    file_name: str,
    max_chars: int = 1200,
    include_prefix: bool = True
) -> list[dict[str, Any]]:
    """규정 본문을 조/부칙/별표/장/절 경계로 청킹합니다. (E1 + E2)

    E1(조항 인식 청킹)만으로는 전체 정확도가 39.6% → 41.7%로 거의 오르지 않았습니다.
    `include_prefix=True`로 문서·조항·날짜 메타를 각 청크 앞에 붙였을 때
    비로소 62.5%로 뛰었습니다(리포트 5.1절). 두 가지는 세트로 써야 합니다.

    Args:
        text (str): HWP에서 추출한 본문 텍스트
        file_name (str): 출처 파일명
        max_chars (int): 청크 최대 길이. 초과 시 항 경계로 추가 분할
        include_prefix (bool): 메타 prefix 부착 여부

    Returns:
        list[dict[str, Any]]: 청크 목록
    """

    normalized = (text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []

    doc_id = parse_document_number(file_name)
    effective_date = parse_effective_date(file_name)

    # lookahead split이라 구분자가 뒤따르는 텍스트에 붙어 나옵니다.
    # 헤더로 시작하지 않는 조각(문서 앞머리의 제목·개정 이력)은 직전 블록에 이어 붙입니다.
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

    chunks: list[dict[str, Any]] = []
    for block in blocks:
        meta = _section_meta(block)
        for piece in _split_long_block(block, max_chars):
            body = piece
            if include_prefix:
                body = f"{_build_prefix(doc_id, effective_date, meta, piece)}\n{piece}"

            chunk = _make_chunk(
                text=body,
                file_name=file_name,
                doc_id=doc_id,
                article=meta["article"],
                section_type=meta["section_type"],
                effective_date=effective_date,
                table_id=meta["table_id"],
            )
            # dates_in_chunk는 prefix가 아니라 원문 기준이어야 중복 집계가 없습니다.
            chunk["dates_in_chunk"] = extract_dates(piece)[:20]
            chunks.append(chunk)

    return chunks


def _is_value_empty_table(rows: list[list[str]], nonempty_cells: Optional[int]) -> bool:
    """표의 값 셀이 비어 있어 수치를 신뢰할 수 없는지 판별합니다.

    rag-test 원본은 `전체 비어있지 않은 셀 수 <= 헤더 열 수`만 검사했습니다.
    그런데 이 조건은 정작 막아야 할 표를 놓칩니다. 실제 봉급표(`1-0-4-T1`)는
    데이터 셀 51개가 전부 비어 있는데도 행·열 라벨(호봉, 급수) 18개가 남아 있어
    `18 <= 5`가 거짓이 되고 경고가 붙지 않았습니다. 리포트 7절이 말하는
    T024 기권은 실제로는 프롬프트 쪽 abstain 지시(리포트 8절 5번)가 막아 준 것입니다.

    그래서 판정 기준을 **헤더 행과 첫 번째 라벨 열을 제외한 값 셀**로 옮겼습니다.
    라벨만 남고 수치가 통째로 빠진 표를 정확히 골라냅니다.

    값 셀이 4개 미만인 작은 표는 대상에서 뺍니다. 표가 아니라 레이아웃 용도로 쓰인
    1~2칸짜리 박스가 많아, 이들까지 경고를 붙이면 정상 표에 노이즈가 됩니다.

    Args:
        rows (list[list[str]]): 표의 행 목록 (첫 행이 헤더)
        nonempty_cells (Optional[int]): 추출 단계에서 센 비어있지 않은 셀 수

    Returns:
        bool: 값 셀이 비어 있다고 판단되면 True
    """

    if not rows:
        return False

    header = rows[0]

    total_nonempty = nonempty_cells
    if total_nonempty is None:
        total_nonempty = sum(1 for row in rows for cell in row if cell.strip())

    # 원본 규칙: 표 전체가 비었거나 헤더 한 줄 분량밖에 없는 퇴화 표.
    if total_nonempty == 0 or total_nonempty <= len(header):
        return True

    # 보강 규칙: 라벨을 제외한 값 셀이 사실상 전부 비어 있는 표.
    value_cells = [cell for row in rows[1:] for cell in row[1:]]
    if len(value_cells) < 4:
        return False

    filled = sum(1 for cell in value_cells if cell.strip())

    return filled / len(value_cells) < 0.1


def chunk_tables(tables: list[dict[str, Any]], file_name: str) -> list[dict[str, Any]]:
    """추출한 표를 Markdown + `[셀요약]` 청크로 변환합니다. (E3)

    `hwp5txt`는 표를 `<표>` placeholder로만 남겨 셀 수치 QA가 구조적으로 불가능했습니다.
    `hwp5html`로 복구한 표를 이 함수로 직렬화해 넣자 표 정확도가 45.8% → 79.2%로,
    hybrid 검색까지 얹으면 87.5%까지 올랐습니다(리포트 5.1절).

    표 하나당 두 가지 표현을 같이 넣습니다.

    1. Markdown 표: 표 전체 구조를 모델이 읽게 합니다.
    2. `[셀요약]` 행별 `헤더=값` 나열: 임베딩 검색이 개별 셀 값에 걸리게 합니다.
       Markdown 표만 넣으면 "행정직 정원이 몇 명인가" 같은 질의가 잘 걸리지 않았습니다.

    Args:
        tables (list[dict[str, Any]]): `extract_tables()` 결과 (`table_id`/`rows`/`nonempty_cells`)
        file_name (str): 출처 파일명

    Returns:
        list[dict[str, Any]]: 표 청크 목록
    """

    doc_id = parse_document_number(file_name)
    effective_date = parse_effective_date(file_name)

    chunks: list[dict[str, Any]] = []
    for table in tables or []:
        rows = table.get("rows") or []
        if not rows:
            continue

        table_id = table.get("table_id")
        header = rows[0]

        lines = [f"[표 {table_id}] 출처:{file_name}"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")

        fact_lines = []
        for row in rows[1:]:
            cells = list(row) + [""] * max(0, len(header) - len(row))
            cells = cells[: len(header)]
            lines.append("| " + " | ".join(cells) + " |")

            pairs = [
                f"{h.strip()}={c.strip()}"
                for h, c in zip(header, cells)
                if c.strip()
            ]
            if pairs:
                fact_lines.append("; ".join(pairs))

        body = "\n".join(lines)
        if fact_lines:
            body += "\n[셀요약]\n" + "\n".join(fact_lines)

        # 봉급 금액 그리드처럼 HWP 단계부터 셀이 비어 있는 표가 있습니다(리포트 6.2절).
        # 이 경고가 없으면 모델이 주변 문맥으로 금액을 지어냅니다. 리포트 7절 T024는
        # 이 문구 덕분에 "모른다"로 기권해 정답 처리된 사례입니다. 절대 제거하지 마세요.
        if _is_value_empty_table(rows, table.get("nonempty_cells")):
            body += "\n" + EMPTY_TABLE_WARNING

        chunks.append(
            _make_chunk(
                text=body,
                file_name=file_name,
                doc_id=doc_id,
                article=table_id,
                section_type="table",
                effective_date=effective_date,
                table_id=table_id,
            )
        )

    return chunks


def chunk_document(
    text: str,
    tables: list[dict[str, Any]],
    file_name: str,
    max_chars: int = 1200
) -> list[dict[str, Any]]:
    """문서 하나의 전체 청크 목록을 만듭니다. (리포트 8절 최종 권장 구성)

    문서메타 청크 1개 + 조항 청크 N개 + 표 청크 M개 순서로 반환합니다.
    이 조합의 extractive 성능은 hybrid top-k=12 기준
    전체 91.7% / 날짜 95.8% / 표 87.5%입니다.

    Args:
        text (str): `hwp_extractor.extract_text()`로 뽑은 본문
        tables (list[dict[str, Any]]): `hwp_extractor.extract_tables()`로 뽑은 표
        file_name (str): 출처 파일명
        max_chars (int): 조항 청크 최대 길이

    Returns:
        list[dict[str, Any]]: 문서 전체 청크 목록
    """

    chunks = [build_document_meta_chunk(file_name, text)]
    chunks.extend(chunk_regulation(text, file_name, max_chars=max_chars, include_prefix=True))
    chunks.extend(chunk_tables(tables, file_name))

    return chunks

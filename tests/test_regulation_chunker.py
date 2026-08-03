"""`app/utils/regulation_chunker.py`와 `app/utils/hwp_extractor.py` 테스트입니다.

합성 문자열이 아니라 `resources/regulations/`의 실제 HWP 코퍼스 181개를 대상으로 합니다.
파일명 규칙이 문서마다 제각각(`+` 구분자, 번호 없는 파일명, 연도 오타)이라
합성 데이터로는 실제 파싱 실패를 잡아낼 수 없기 때문입니다.
"""

import os
import unicodedata

from pathlib import Path

import pytest

from app.utils.regulation_chunker import (
    EMPTY_TABLE_WARNING,
    build_document_meta_chunk,
    chunk_document,
    chunk_regulation,
    chunk_tables,
    extract_dates,
    parse_document_number,
    parse_effective_date,
)


REGULATIONS_DIR = Path(__file__).resolve().parents[1] / "resources" / "regulations"

# 시행일 파싱 성공률 하한. 파일명 시행일은 문서메타 청크의 핵심 값이고,
# 이 값이 빠지면 해당 문서의 날짜 질의 정확도가 79% → 33% 수준으로 떨어집니다.
MIN_EFFECTIVE_DATE_RATE = 0.90

# 조항 경계 검증에 쓸 문서. 조/장/부칙/별표가 모두 들어 있는 대표 문서입니다.
SAMPLE_DOCUMENTS = ("2-0-1", "1-0-1")


def _corpus_file_names() -> list[str]:
    """코퍼스의 HWP 파일명을 NFC로 정규화해 반환합니다.

    Returns:
        list[str]: 파일명 목록
    """

    if not REGULATIONS_DIR.is_dir():
        return []

    return sorted(
        unicodedata.normalize("NFC", name)
        for name in os.listdir(REGULATIONS_DIR)
        if name.lower().endswith(".hwp")
    )


def _corpus_path(prefix: str) -> Path:
    """문서번호 접두어로 실제 HWP 경로를 찾습니다.

    디스크 파일명이 NFD로 저장돼 있어 NFC 문자열과 직접 비교하면 어긋납니다.

    Args:
        prefix (str): 문서번호 접두어 (예: "2-0-1")

    Returns:
        Path: 실제 파일 경로
    """

    for name in sorted(os.listdir(REGULATIONS_DIR)):
        if unicodedata.normalize("NFC", name).startswith(prefix):
            return REGULATIONS_DIR / name

    pytest.skip(f"코퍼스에 {prefix} 문서가 없습니다.")


@pytest.fixture(scope="module")
def file_names() -> list[str]:
    """코퍼스 파일명 목록 fixture입니다."""

    names = _corpus_file_names()
    if not names:
        pytest.skip(f"코퍼스 디렉터리가 없습니다: {REGULATIONS_DIR}")

    return names


@pytest.fixture(scope="module")
def sample_text() -> str:
    """대표 문서(학칙)의 본문 텍스트 fixture입니다."""

    from app.utils.hwp_extractor import extract_text

    text = extract_text(_corpus_path(SAMPLE_DOCUMENTS[0]))
    if not text:
        pytest.skip("hwp5txt로 본문을 추출하지 못했습니다.")

    return text


class TestFileNameParsing:
    """파일명 메타데이터 파싱 테스트입니다."""

    def test_effective_date_parse_rate(self, file_names):
        """실제 코퍼스 181개의 시행일 파싱 성공률이 하한을 넘는지 검증합니다."""

        failed = [name for name in file_names if parse_effective_date(name) is None]
        rate = 1 - len(failed) / len(file_names)

        assert rate >= MIN_EFFECTIVE_DATE_RATE, (
            f"시행일 파싱 성공률 {rate:.1%} < 목표 {MIN_EFFECTIVE_DATE_RATE:.0%} "
            f"({len(failed)}/{len(file_names)} 실패)\n실패 파일:\n  "
            + "\n  ".join(failed)
        )

    def test_effective_dates_are_iso_and_plausible(self, file_names):
        """파싱된 시행일이 ISO 형식이고 상식적인 범위인지 검증합니다.

        형식만 맞고 값이 틀린 날짜는 답변에 그대로 인용되므로 실패보다 위험합니다.
        """

        bad = []
        for name in file_names:
            iso = parse_effective_date(name)
            if iso is None:
                continue
            year = int(iso[:4])
            if not (1950 <= year <= 2035):
                bad.append(f"{name} -> {iso}")

        assert not bad, "시행일 연도가 비상식적입니다:\n  " + "\n  ".join(bad)

    def test_document_number_variants(self):
        """`1-0-*` 외의 번호 체계도 파싱되는지 검증합니다.

        원본 rag-test 구현은 `1-0-\\d+`로 하드코딩돼 있어 나머지를 전부 놓쳤습니다.
        """

        assert parse_document_number("1-0-1학교법인계명대학교정관(시행2026.7.1.).hwp") == "1-0-1"
        assert parse_document_number("2-0-1학칙(시행2026.3.1.).hwp") == "2-0-1"
        assert parse_document_number("3-1-10시설관리규정(시행2020.12.15.).hwp") == "3-1-10"
        assert parse_document_number("3-4-2대학원학칙(시행2026.3.1.).hwp") == "3-4-2"
        assert parse_document_number("5-1-1규정(시행2020.1.1.).hwp") == "5-1-1"

    def test_document_number_absent_returns_none(self):
        """번호 없는 파일명은 None을 반환하되 예외를 내지 않아야 합니다."""

        name = "JA(Joint+Appointment)교원운영내규(시행2025.11.1.).hwp"

        assert parse_document_number(name) is None
        # 번호가 없어도 시행일은 정상 파싱돼야 합니다.
        assert parse_effective_date(name) == "2025-11-01"

    def test_plus_separated_file_names(self):
        """공백이 `+`로 치환된 파일명도 파싱되는지 검증합니다."""

        assert parse_effective_date(
            "학교법인+계명대학교+문서관리+규정(시행+2018.+7.+1).hwp"
        ) == "2018-07-01"
        assert parse_effective_date(
            "1-0-6+학교법인+계명대학교+교직원+명예퇴직+운영+규칙(시행+2016.+1.+1.).hwp"
        ) == "2016-01-01"

    def test_malformed_year_is_rejected(self):
        """연도가 5자리인 오타 파일명은 그럴듯한 오답 대신 None을 반환해야 합니다."""

        assert parse_effective_date("3-1-35+에너지+관리+규정(시행+20108.+7.+9.).hwp") is None

    def test_all_document_numbers_are_wellformed(self, file_names):
        """코퍼스에서 파싱된 문서번호가 모두 `N-N-N` 형태인지 검증합니다."""

        import re

        for name in file_names:
            doc_id = parse_document_number(name)
            if doc_id is not None:
                assert re.fullmatch(r"\d+-\d+-\d+", doc_id), f"{name} -> {doc_id}"


class TestExtractDates:
    """본문 날짜 추출 테스트입니다."""

    def test_common_date_formats(self):
        """규정 본문에 나타나는 여러 날짜 표기를 모두 인식해야 합니다."""

        text = "변경(2018. 7. 1.인가) 2020-03-15 시행 1995년 9월 26일 개정"

        assert extract_dates(text) == ["2018-07-01", "2020-03-15", "1995-09-26"]

    def test_invalid_date_is_dropped(self):
        """존재하지 않는 날짜는 버려야 합니다."""

        assert extract_dates("2020. 13. 45.") == []

    def test_empty_input(self):
        """빈 입력에서 예외가 나지 않아야 합니다."""

        assert extract_dates("") == []


class TestDocumentMetaChunk:
    """문서메타 청크 테스트입니다. (E2 — 날짜 정확도 33% → 79%의 근거)"""

    def test_exactly_one_meta_chunk_per_document(self, sample_text):
        """문서당 메타 청크는 정확히 1개여야 합니다."""

        file_name = "2-0-1학칙(시행2026.3.1.).hwp"
        chunks = chunk_document(sample_text, [], file_name)
        metas = [c for c in chunks if c["section_type"] == "meta"]

        assert len(metas) == 1, f"메타 청크가 {len(metas)}개입니다 (기대: 1개)"

    def test_meta_chunk_contains_filename_effective_date(self):
        """메타 청크가 본문에 없는 파일명 시행일을 담고 있어야 합니다."""

        file_name = "2-0-1학칙(시행2026.3.1.).hwp"
        chunk = build_document_meta_chunk(file_name, "학칙\n제1조(목적) ...")

        assert chunk["section_type"] == "meta"
        assert chunk["effective_date"] == "2026-03-01"
        assert "[문서메타]" in chunk["text"]
        assert "문서번호=2-0-1" in chunk["text"]
        assert "파일명시행일=2026-03-01" in chunk["text"]
        assert "제목=학칙" in chunk["text"]
        assert "2026-03-01" in chunk["dates_in_chunk"]

    def test_meta_chunk_without_document_number(self):
        """문서번호가 없어도 메타 청크가 만들어져야 합니다."""

        chunk = build_document_meta_chunk(
            "JA(Joint+Appointment)교원운영내규(시행2025.11.1.).hwp", "JA 교원 운영 내규"
        )

        assert chunk["doc_id"] is None
        assert chunk["effective_date"] == "2025-11-01"

    def test_meta_chunk_warns_when_date_unknown(self):
        """시행일을 알 수 없으면 추정 금지 문구가 들어가야 합니다."""

        chunk = build_document_meta_chunk("이름만있는규정.hwp", "어떤 규정")

        assert chunk["effective_date"] is None
        assert "추정하지 마세요" in chunk["text"]


class TestArticleChunking:
    """조항 경계 청킹 테스트입니다. (E1)"""

    def test_article_boundaries_survive(self, sample_text):
        """`제N조` 헤더가 청크 경계로 살아 있어야 합니다."""

        chunks = chunk_regulation(sample_text, "2-0-1학칙(시행2026.3.1.).hwp")
        articles = [c for c in chunks if c["section_type"] == "article"]

        assert len(articles) >= 20, f"조항 청크가 {len(articles)}개뿐입니다."
        for chunk in articles:
            assert chunk["article"].startswith("제"), chunk["article"]
            assert "조" in chunk["article"]

    def test_addendum_and_attachment_are_separate_sections(self, sample_text):
        """부칙과 별표는 조문과 분리된 청크여야 합니다.

        부칙 시행일과 조항 개정일이 한 청크에 섞이면 모델이 둘을 혼동합니다.
        """

        chunks = chunk_regulation(sample_text, "2-0-1학칙(시행2026.3.1.).hwp")
        types = {c["section_type"] for c in chunks}

        assert "addendum" in types, "부칙 청크가 분리되지 않았습니다."
        assert "attachment" in types, "별표 청크가 분리되지 않았습니다."

        for chunk in chunks:
            if chunk["section_type"] == "addendum":
                assert chunk["article"] == "부칙"
            if chunk["section_type"] == "attachment":
                assert chunk["article"].startswith("[별표")

    def test_prefix_carries_document_metadata(self, sample_text):
        """`include_prefix=True`면 각 청크에 문서·조항 메타가 붙어야 합니다."""

        chunks = chunk_regulation(sample_text, "2-0-1학칙(시행2026.3.1.).hwp", include_prefix=True)

        assert chunks
        for chunk in chunks[:20]:
            assert chunk["text"].startswith("[문서:2-0-1 | 파일명시행일:2026-03-01"), chunk["text"][:80]

    def test_prefix_can_be_disabled(self, sample_text):
        """`include_prefix=False`면 원문만 남아야 합니다."""

        chunks = chunk_regulation(sample_text, "2-0-1학칙(시행2026.3.1.).hwp", include_prefix=False)

        assert chunks
        assert not any(c["text"].startswith("[문서:") for c in chunks)

    def test_chunks_respect_max_chars(self, sample_text):
        """분할 후 청크 길이가 상한을 크게 넘지 않아야 합니다."""

        max_chars = 800
        chunks = chunk_regulation(
            sample_text, "2-0-1학칙(시행2026.3.1.).hwp", max_chars=max_chars, include_prefix=False
        )

        oversized = [c for c in chunks if len(c["text"]) > max_chars]

        assert not oversized, f"{len(oversized)}개 청크가 {max_chars}자를 초과했습니다."

    def test_second_document_chunks(self):
        """다른 문서(정관)에서도 조항 청킹이 동작해야 합니다."""

        from app.utils.hwp_extractor import extract_text

        path = _corpus_path(SAMPLE_DOCUMENTS[1])
        text = extract_text(path)
        if not text:
            pytest.skip("hwp5txt로 정관 본문을 추출하지 못했습니다.")

        chunks = chunk_regulation(text, unicodedata.normalize("NFC", path.name))
        articles = [c for c in chunks if c["section_type"] == "article"]

        assert len(articles) >= 10
        assert all(c["doc_id"] == "1-0-1" for c in chunks)

    def test_empty_text_returns_no_chunks(self):
        """빈 본문은 빈 목록을 반환해야 합니다."""

        assert chunk_regulation("", "2-0-1학칙(시행2026.3.1.).hwp") == []
        assert chunk_regulation("   \n  ", "2-0-1학칙(시행2026.3.1.).hwp") == []


class TestTableChunking:
    """표 청킹 테스트입니다. (E3 — 표 정확도 45.8% → 79.2%의 근거)"""

    def test_markdown_and_cell_summary(self):
        """표가 Markdown과 `[셀요약]` 두 형태로 직렬화돼야 합니다."""

        tables = [
            {
                "table_id": "1-0-1-T1",
                "rows": [["직종", "정원"], ["행 정 직", "18"], ["관리운영직", "3"]],
                "nonempty_cells": 6,
            }
        ]
        chunks = chunk_tables(tables, "1-0-1학교법인계명대학교정관(시행2026.7.1.).hwp")

        assert len(chunks) == 1
        chunk = chunks[0]

        assert chunk["section_type"] == "table"
        assert chunk["table_id"] == "1-0-1-T1"
        assert chunk["doc_id"] == "1-0-1"
        assert chunk["effective_date"] == "2026-07-01"
        assert "| 직종 | 정원 |" in chunk["text"]
        assert "| --- | --- |" in chunk["text"]
        assert "[셀요약]" in chunk["text"]
        assert "직종=행 정 직; 정원=18" in chunk["text"]
        assert EMPTY_TABLE_WARNING not in chunk["text"]

    def test_empty_cell_table_gets_warning(self):
        """수치 셀이 빈 표에는 금액 추정 금지 경고가 붙어야 합니다.

        리포트 7절 T024(별표 6-1 봉급 금액)는 HWP 단계부터 셀이 비어 있었고,
        이 경고 덕분에 모델이 금액을 지어내지 않고 기권해 정답 처리됐습니다.
        """

        tables = [
            {
                "table_id": "1-0-4-T3",
                "rows": [["호봉", "1급", "2급"], ["1", "", ""], ["2", "", ""]],
                "nonempty_cells": 5,
            }
        ]
        chunks = chunk_tables(tables, "1-0-4계명대학교교직원보수규칙(시행2026.3.1.).hwp")

        assert EMPTY_TABLE_WARNING in chunks[0]["text"]
        assert "금액을 추정하지 마세요" in chunks[0]["text"]

    def test_fully_empty_table_gets_warning(self):
        """모든 셀이 빈 표에도 경고가 붙어야 합니다."""

        tables = [
            {
                "table_id": "X-T0",
                "rows": [["", "", ""], ["", "", ""]],
                "nonempty_cells": 0,
            }
        ]
        chunks = chunk_tables(tables, "3-1-10시설관리규정(시행2020.12.15.).hwp")

        assert EMPTY_TABLE_WARNING in chunks[0]["text"]

    def test_ragged_rows_are_padded(self):
        """헤더보다 셀이 적은 행도 Markdown 열 수를 맞춰야 합니다."""

        tables = [
            {
                "table_id": "T0",
                "rows": [["a", "b", "c"], ["1"], ["1", "2", "3", "4"]],
                "nonempty_cells": 8,
            }
        ]
        chunks = chunk_tables(tables, "3-1-10시설관리규정(시행2020.12.15.).hwp")
        body = chunks[0]["text"]

        assert "| 1 |  |  |" in body
        assert "| 1 | 2 | 3 |" in body

    def test_no_tables_returns_empty(self):
        """표가 없으면 빈 목록을 반환해야 합니다."""

        assert chunk_tables([], "2-0-1학칙(시행2026.3.1.).hwp") == []
        assert chunk_tables(None, "2-0-1학칙(시행2026.3.1.).hwp") == []


class TestChunkDocument:
    """문서 전체 청킹 통합 테스트입니다."""

    def test_chunk_fields_are_complete(self, sample_text):
        """모든 청크가 규정된 필드를 갖춰야 합니다."""

        tables = [{"table_id": "T0", "rows": [["a", "b"], ["1", "2"]], "nonempty_cells": 4}]
        chunks = chunk_document(sample_text, tables, "2-0-1학칙(시행2026.3.1.).hwp")

        expected = {
            "text",
            "source",
            "doc_id",
            "article",
            "section_type",
            "effective_date",
            "dates_in_chunk",
            "table_id",
        }
        for chunk in chunks:
            assert set(chunk) == expected
            assert chunk["source"] == "2-0-1학칙(시행2026.3.1.).hwp"
            assert chunk["text"].strip()

    def test_meta_chunk_comes_first(self, sample_text):
        """메타 청크가 목록 맨 앞이어야 합니다."""

        chunks = chunk_document(sample_text, [], "2-0-1학칙(시행2026.3.1.).hwp")

        assert chunks[0]["section_type"] == "meta"

    def test_table_chunks_are_appended(self, sample_text):
        """표 청크가 함께 포함돼야 합니다."""

        tables = [{"table_id": "T0", "rows": [["a", "b"], ["1", "2"]], "nonempty_cells": 4}]
        chunks = chunk_document(sample_text, tables, "2-0-1학칙(시행2026.3.1.).hwp")

        assert sum(1 for c in chunks if c["section_type"] == "table") == 1


class TestHwpExtractor:
    """HWP 추출 테스트입니다."""

    def test_extract_text_from_real_document(self, tmp_path):
        """실제 HWP에서 본문을 추출해야 합니다."""

        from app.utils.hwp_extractor import extract_text

        text = extract_text(_corpus_path(SAMPLE_DOCUMENTS[0]), cache_dir=tmp_path)

        assert text and len(text) > 1000
        assert "제1조" in text

    def test_text_cache_is_reused(self, tmp_path):
        """두 번째 호출은 캐시 파일을 재사용해야 합니다."""

        from app.utils.hwp_extractor import extract_text

        path = _corpus_path(SAMPLE_DOCUMENTS[0])
        first = extract_text(path, cache_dir=tmp_path)
        cache_files = list(tmp_path.glob("*.txt"))

        assert len(cache_files) == 1

        # 캐시를 직접 조작해 실제로 캐시에서 읽는지 확인합니다.
        cache_files[0].write_text("캐시된 본문입니다.", encoding="utf-8")

        assert extract_text(path, cache_dir=tmp_path) == "캐시된 본문입니다."
        assert extract_text(path, cache_dir=tmp_path, use_cache=False) == first

    def test_missing_file_returns_none_without_raising(self, tmp_path):
        """없는 파일은 예외 대신 None을 반환해야 합니다.

        문서 한 개의 실패가 181개 적재 전체를 멈추면 안 됩니다.
        """

        from app.utils.hwp_extractor import extract_text, extract_tables

        assert extract_text(tmp_path / "없는파일.hwp") is None
        assert extract_tables(tmp_path / "없는파일.hwp") == []

    def test_corrupt_file_returns_none_without_raising(self, tmp_path):
        """HWP가 아닌 파일도 예외 대신 None/빈 목록을 반환해야 합니다."""

        from app.utils.hwp_extractor import extract_text, extract_tables

        broken = tmp_path / "broken.hwp"
        broken.write_bytes(b"not an ole file at all")

        assert extract_text(broken, cache_dir=tmp_path / "cache") is None
        assert extract_tables(broken) == []

    def test_nfc_path_resolves_to_nfd_file(self, tmp_path):
        """NFC 경로로도 NFD 저장된 실제 파일을 찾아야 합니다."""

        from app.utils.hwp_extractor import extract_text

        path = _corpus_path(SAMPLE_DOCUMENTS[0])
        nfc_path = path.parent / unicodedata.normalize("NFC", path.name)

        assert extract_text(nfc_path, cache_dir=tmp_path) is not None

    def test_extract_tables_from_real_document(self):
        """실제 HWP에서 표 구조를 복구해야 합니다."""

        from app.utils.hwp_extractor import extract_tables

        tables = extract_tables(_corpus_path(SAMPLE_DOCUMENTS[1]), doc_id="1-0-1")
        if not tables:
            pytest.skip("hwp5html로 표를 추출하지 못했습니다.")

        assert all(t["table_id"].startswith("1-0-1-T") for t in tables)
        assert all(isinstance(t["rows"], list) and t["rows"] for t in tables)
        assert all(isinstance(t["nonempty_cells"], int) for t in tables)

        # 정관에는 직종/정원 표가 있어야 하며 셀 값이 복구돼야 합니다.
        flattened = [cell for t in tables for row in t["rows"] for cell in row]

        assert "정원" in flattened

    def test_extracted_tables_chunk_end_to_end(self):
        """추출한 표를 그대로 청킹에 넘길 수 있어야 합니다."""

        from app.utils.hwp_extractor import extract_tables

        path = _corpus_path(SAMPLE_DOCUMENTS[1])
        tables = extract_tables(path, doc_id="1-0-1")
        if not tables:
            pytest.skip("hwp5html로 표를 추출하지 못했습니다.")

        chunks = chunk_tables(tables, unicodedata.normalize("NFC", path.name))

        assert len(chunks) == len(tables)
        assert all(c["section_type"] == "table" for c in chunks)


@pytest.mark.asyncio
async def test_async_wrappers(tmp_path):
    """비동기 변형이 동일한 결과를 반환해야 합니다."""

    from app.utils.hwp_extractor import extract_text, extract_text_async, extract_tables_async

    path = _corpus_path(SAMPLE_DOCUMENTS[0])

    assert await extract_text_async(path, cache_dir=tmp_path) == extract_text(path, cache_dir=tmp_path)
    assert await extract_tables_async(tmp_path / "없는파일.hwp") == []

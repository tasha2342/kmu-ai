"""hybrid 검색 융합 로직과 규정 적재 포인트 변환 테스트입니다.

검색 융합은 `app/utils/vector_store.py`의 순수 함수(`parse_query_signals`,
`min_max_normalize`, `compute_boost`, `fuse_hybrid_scores`)로 분리돼 있어
PostgreSQL 없이도 정규화·가중합·boost 동작을 그대로 검증할 수 있습니다.

가중치(0.55/0.45)와 top-k(12)는 `rag-test/docs/rag_test_report.md`의 골드셋 48문항
측정값이므로, 값이 바뀌면 테스트가 깨져 "왜 바꿨는지"를 되묻게 만듭니다.
"""

from pathlib import Path

import pytest

from app.utils.regulation_ingest import (
    REGULATION_COLLECTION_NAME,
    build_regulation_points,
    compute_source_hash,
    list_regulation_files,
    make_point_id,
    make_source_id,
    prepare_regulation_chunks,
)
from app.utils.vector_store import (
    ARTICLE_BOOST,
    DEFAULT_HYBRID_TOP_K,
    DENSE_WEIGHT,
    DOC_ID_BOOST,
    LEXICAL_WEIGHT,
    compute_boost,
    fuse_hybrid_scores,
    min_max_normalize,
    parse_query_signals,
)


REGULATIONS_DIR = Path(__file__).resolve().parents[1] / "resources" / "regulations"


# ===== 리포트 측정값 고정 =====

def test_report_derived_constants():
    """가중치와 기본 top-k가 리포트 E4 최적 구성과 일치해야 합니다."""

    # 리포트 8절: hybrid(dense 0.55 + BM25 0.45) + 문서/조항 boost, top-k=12
    assert DENSE_WEIGHT == 0.55
    assert LEXICAL_WEIGHT == 0.45
    assert DENSE_WEIGHT + LEXICAL_WEIGHT == pytest.approx(1.0)
    assert DEFAULT_HYBRID_TOP_K == 12


# ===== 질의 신호 파싱 =====

def test_parse_query_signals_document_number():
    """질의에 박힌 문서번호를 뽑아야 합니다."""

    signals = parse_query_signals("3-1-10 규정의 시행일이 언제인가요?")

    assert signals["doc_ids"] == ["3-1-10"]
    assert signals["articles"] == []


def test_parse_query_signals_article_variants():
    """조/조의N 표기와 공백이 섞여도 정규 표기로 정규화해야 합니다."""

    signals = parse_query_signals("제15조와 제 3 조의 2 를 알려줘")

    assert signals["articles"] == ["제15조", "제3조의2"]


def test_parse_query_signals_deduplicates():
    """같은 신호가 여러 번 나와도 한 번만 담아야 합니다."""

    signals = parse_query_signals("1-0-1 제7조, 1-0-1 제7조 다시")

    assert signals["doc_ids"] == ["1-0-1"]
    assert signals["articles"] == ["제7조"]


def test_parse_query_signals_without_signal():
    """일반 질의에서는 신호가 없어야 합니다. (오탐 방지)"""

    signals = parse_query_signals("휴학은 몇 년까지 가능한가요?")

    assert signals == {"doc_ids": [], "articles": []}


def test_parse_query_signals_ignores_dates():
    """`2024-03-01` 같은 날짜를 문서번호로 오인하면 안 됩니다.

    규정 질의에는 시행일·개정일이 자주 등장합니다. 날짜를 문서번호로 잡으면
    존재하지 않는 문서에 가산점을 주게 되어 boost 자체가 무의미해집니다.
    """

    assert parse_query_signals("2024-03-01 시행 내용")["doc_ids"] == []
    assert parse_query_signals("1999-1-1에 개정된 조항")["doc_ids"] == []
    # 실제 문서번호는 그대로 잡혀야 합니다.
    assert parse_query_signals("3-1-10 문서")["doc_ids"] == ["3-1-10"]
    assert parse_query_signals("1-0-1 정관")["doc_ids"] == ["1-0-1"]


# ===== 정규화 =====

def test_min_max_normalize_scales_to_unit_range():
    """최솟값 0, 최댓값 1로 선형 정규화해야 합니다."""

    assert min_max_normalize([0.2, 0.4, 0.6]) == pytest.approx([0.0, 0.5, 1.0])


def test_min_max_normalize_empty():
    """빈 목록은 빈 목록을 돌려줘야 합니다."""

    assert min_max_normalize([]) == []


def test_min_max_normalize_identical_values():
    """모든 값이 같으면 1.0으로 채워야 합니다.

    0.0으로 채우면 해당 검색기가 찾아낸 유일한 후보가 융합 점수에 전혀 기여하지 못합니다.
    """

    assert min_max_normalize([0.7, 0.7, 0.7]) == [1.0, 1.0, 1.0]
    assert min_max_normalize([3.5]) == [1.0]


def test_min_max_normalize_absorbs_scale_difference():
    """스케일이 크게 달라도 같은 0~1 구간으로 맞춰야 합니다.

    dense는 코사인 유사도(0~1), 어휘는 `ts_rank_cd`(상한 없음)라 정규화 없이 더하면
    어휘 점수가 가중치를 무시하고 결과를 지배합니다.
    """

    assert min_max_normalize([0.51, 0.55, 0.62])[-1] == 1.0
    assert min_max_normalize([0.02, 4.0, 91.0])[-1] == 1.0


# ===== boost =====

def test_compute_boost_exact_matches():
    """문서번호·조항이 정확히 일치할 때만 가산점을 줘야 합니다."""

    signals = parse_query_signals("3-1-10 제15조")

    both = compute_boost({"doc_id": "3-1-10", "article": "제15조"}, signals)
    doc_only = compute_boost({"doc_id": "3-1-10", "article": "제9조"}, signals)
    article_only = compute_boost({"doc_id": "1-0-1", "article": "제15조"}, signals)
    neither = compute_boost({"doc_id": "1-0-1", "article": "제9조"}, signals)

    assert both == pytest.approx(DOC_ID_BOOST + ARTICLE_BOOST)
    assert doc_only == pytest.approx(DOC_ID_BOOST)
    assert article_only == pytest.approx(ARTICLE_BOOST)
    assert neither == 0.0


def test_compute_boost_skips_addendum_chunk():
    """부칙 청크는 조항 질의에서 가산점을 받지 못해야 합니다.

    리포트 6.3절의 "부칙 청크 과다 노출"을 완화하는 핵심 장치입니다.
    부칙 청크는 본문에 `제15조`가 언급돼 있어도 메타데이터 `article`이 `부칙`입니다.
    """

    signals = parse_query_signals("제15조 내용 알려줘")

    article_chunk = compute_boost(
        {"doc_id": "1-0-1", "article": "제15조", "section_type": "article"}, signals
    )
    addendum_chunk = compute_boost(
        {"doc_id": "1-0-1", "article": "부칙", "section_type": "addendum"}, signals
    )

    assert article_chunk > addendum_chunk
    assert addendum_chunk == 0.0


def test_compute_boost_missing_metadata():
    """메타데이터가 없는 payload에서도 예외 없이 0을 돌려줘야 합니다."""

    assert compute_boost({}, parse_query_signals("3-1-10")) == 0.0
    assert compute_boost({"doc_id": None, "article": None}, {"doc_ids": [], "articles": []}) == 0.0


# ===== 융합 =====

def test_fuse_hybrid_scores_weighted_sum():
    """정규화 후 0.55/0.45 가중합이 그대로 계산돼야 합니다."""

    fused = fuse_hybrid_scores(
        dense_scores={"a": 0.9, "b": 0.5},
        lexical_scores={"a": 0.1, "b": 0.9},
    )
    scores = {item["id"]: item["score"] for item in fused}

    # a: dense 1.0 / lexical 0.0 -> 0.55, b: dense 0.0 / lexical 1.0 -> 0.45
    assert scores["a"] == pytest.approx(DENSE_WEIGHT)
    assert scores["b"] == pytest.approx(LEXICAL_WEIGHT)
    assert [item["id"] for item in fused] == ["a", "b"]


def test_fuse_hybrid_scores_rewards_both_retrievers():
    """양쪽 검색기가 모두 찾은 청크가 한쪽 1위보다 위로 올라와야 합니다.

    hybrid가 Recall을 끌어올리는(81.3% → 89.6%) 핵심 동작입니다.
    후보군이 2건뿐이면 min-max 특성상 하위 1건이 무조건 0점이 되므로,
    실제 검색과 같이 후보를 넉넉히 둔 상태로 검증합니다.
    """

    fused = fuse_hybrid_scores(
        dense_scores={"both": 0.80, "dense_only": 0.90, "tail": 0.60},
        lexical_scores={"both": 0.50, "lexical_only": 0.60, "tail2": 0.30},
    )

    assert fused[0]["id"] == "both"
    assert {item["id"] for item in fused[1:3]} == {"dense_only", "lexical_only"}


def test_fuse_hybrid_scores_missing_side_is_zero():
    """상대 후보군에 없는 청크는 그 쪽 정규화 점수가 0이어야 합니다."""

    fused = fuse_hybrid_scores(
        dense_scores={"a": 0.9},
        lexical_scores={"b": 4.2},
    )
    by_id = {item["id"]: item for item in fused}

    assert by_id["a"]["lexical_norm"] == 0.0
    assert by_id["a"]["lexical_score"] is None
    assert by_id["b"]["dense_norm"] == 0.0
    assert by_id["b"]["dense_score"] is None


def test_fuse_hybrid_scores_lexical_scale_does_not_dominate():
    """어휘 원점수가 아무리 커도 가중치 비율을 넘어서면 안 됩니다."""

    fused = fuse_hybrid_scores(
        dense_scores={"a": 0.95, "b": 0.10},
        lexical_scores={"a": 0.01, "b": 900.0},
    )

    # 정규화가 없으면 b가 900점으로 압도했겠지만, 가중치대로 a(0.55) > b(0.45)여야 합니다.
    assert fused[0]["id"] == "a"
    assert fused[0]["score"] == pytest.approx(DENSE_WEIGHT)


def test_fuse_hybrid_scores_boost_reranks():
    """boost가 근소한 차이를 뒤집어 지정 조항 청크를 위로 올려야 합니다.

    리포트 6.3절의 "부칙 청크 과다 노출" 상황입니다. 부칙 청크는 개정 이력 날짜를
    잔뜩 품고 있어 유사도가 근소하게 높게 나오지만, 사용자가 지정한 조문은 따로 있습니다.
    """

    dense = {"addendum": 0.88, "article": 0.86, "other1": 0.70, "other2": 0.60, "other3": 0.50}
    lexical = {"addendum": 2.0, "article": 1.9, "other1": 1.0, "other2": 0.5, "other3": 0.1}

    without_boost = fuse_hybrid_scores(dense_scores=dense, lexical_scores=lexical)
    with_boost = fuse_hybrid_scores(
        dense_scores=dense,
        lexical_scores=lexical,
        boosts={"article": DOC_ID_BOOST + ARTICLE_BOOST},
    )

    assert without_boost[0]["id"] == "addendum"
    assert with_boost[0]["id"] == "article"
    assert with_boost[0]["boost"] == pytest.approx(DOC_ID_BOOST + ARTICLE_BOOST)


def test_fuse_hybrid_scores_boost_does_not_override_everything():
    """boost는 재순위 장치일 뿐, 무관한 청크를 1위로 밀어 올리면 안 됩니다."""

    fused = fuse_hybrid_scores(
        dense_scores={"top": 0.95, "weak": 0.30},
        lexical_scores={"top": 5.0, "weak": 0.1},
        boosts={"weak": DOC_ID_BOOST + ARTICLE_BOOST},
    )

    # 양쪽 검색기 모두에서 최하위인 청크는 가산점(최대 0.25)만으로 1.0을 넘지 못합니다.
    assert fused[0]["id"] == "top"


def test_fuse_hybrid_scores_limit():
    """상위 `limit`건만 돌려줘야 합니다."""

    dense = {f"c{index}": 1.0 - index * 0.01 for index in range(30)}
    fused = fuse_hybrid_scores(dense_scores=dense, lexical_scores={}, limit=12)

    assert len(fused) == 12
    assert fused[0]["id"] == "c0"


def test_fuse_hybrid_scores_empty_candidates():
    """후보가 하나도 없으면 빈 목록이어야 합니다."""

    assert fuse_hybrid_scores(dense_scores={}, lexical_scores={}) == []


def test_fuse_hybrid_scores_debug_fields():
    """디버깅용 원점수가 결과에 함께 담겨야 합니다."""

    fused = fuse_hybrid_scores(
        dense_scores={"a": 0.9, "b": 0.4},
        lexical_scores={"a": 3.0, "b": 1.0},
    )
    first = fused[0]

    assert first["dense_score"] == 0.9
    assert first["lexical_score"] == 3.0
    assert set(first) == {
        "id", "score", "dense_score", "lexical_score", "dense_norm", "lexical_norm", "boost",
    }


# ===== 규정 적재 포인트 변환 =====

def _sample_chunks() -> list[dict]:
    """청킹 결과 형태의 샘플 청크를 만듭니다."""

    return [
        {
            "text": "[문서메타] 문서번호=3-1-10 | 파일명시행일=2024-03-01",
            "source": "3-1-10규정(시행2024.3.1.).hwp",
            "doc_id": "3-1-10",
            "article": "문서메타",
            "section_type": "meta",
            "effective_date": "2024-03-01",
            "dates_in_chunk": [],
            "table_id": None,
        },
        {
            "text": "제15조(휴학) 학생은 휴학할 수 있다.",
            "source": "3-1-10규정(시행2024.3.1.).hwp",
            "doc_id": "3-1-10",
            "article": "제15조",
            "section_type": "article",
            "effective_date": "2024-03-01",
            "dates_in_chunk": ["2018-07-01"],
            "table_id": None,
        },
    ]


def test_build_regulation_points_payload_shape():
    """청킹 메타데이터가 포인트 payload에 그대로 실려야 합니다."""

    chunks = _sample_chunks()
    points = build_regulation_points(
        chunks=chunks,
        vectors=[[0.1] * 4, [0.2] * 4],
        document_id=7,
        file_name="3-1-10규정(시행2024.3.1.).hwp",
    )

    assert len(points) == 2

    payload = points[1]["payload"]
    assert payload["document_id"] == 7
    assert payload["chunk_index"] == 1
    assert payload["content"] == chunks[1]["text"]
    assert payload["doc_id"] == "3-1-10"
    assert payload["article"] == "제15조"
    assert payload["section_type"] == "article"
    assert payload["effective_date"] == "2024-03-01"
    assert payload["dates_in_chunk"] == ["2018-07-01"]
    assert payload["table_id"] is None
    assert payload["page"] is None


def test_build_regulation_points_payload_feeds_boost():
    """포인트 payload가 그대로 boost 입력으로 쓰일 수 있어야 합니다."""

    points = build_regulation_points(
        chunks=_sample_chunks(),
        vectors=[[0.1] * 4, [0.2] * 4],
        document_id=7,
        file_name="3-1-10규정(시행2024.3.1.).hwp",
    )
    signals = parse_query_signals("3-1-10 제15조 휴학")

    assert compute_boost(points[1]["payload"], signals) == pytest.approx(DOC_ID_BOOST + ARTICLE_BOOST)


def test_build_regulation_points_ids_are_deterministic():
    """재색인해도 같은 청크는 같은 포인트 ID여야 합니다. (중복 누적 방지)"""

    args = {
        "chunks": _sample_chunks(),
        "vectors": [[0.1] * 4, [0.2] * 4],
        "document_id": 7,
        "file_name": "3-1-10규정(시행2024.3.1.).hwp",
    }
    first = build_regulation_points(**args)
    second = build_regulation_points(**args)

    assert [point["id"] for point in first] == [point["id"] for point in second]
    assert first[0]["id"] != first[1]["id"]
    assert first[1]["id"] == make_point_id("3-1-10규정(시행2024.3.1.).hwp", 1)


def test_build_regulation_points_rejects_length_mismatch():
    """청크 수와 벡터 수가 다르면 조용히 잘라내지 말고 실패해야 합니다."""

    with pytest.raises(ValueError):
        build_regulation_points(
            chunks=_sample_chunks(),
            vectors=[[0.1] * 4],
            document_id=7,
            file_name="3-1-10규정(시행2024.3.1.).hwp",
        )


def test_source_id_is_stable_and_nfc_insensitive():
    """파일명 기반 원천 ID는 유니코드 정규화 형태와 무관하게 같아야 합니다.

    코퍼스 파일명이 디스크에 NFD로 저장돼 있어, 정규화하지 않으면 같은 파일이
    실행할 때마다 다른 `source_id`로 기록될 수 있습니다.
    """

    import unicodedata

    name = "1-0-1학교법인계명대학교정관(시행2026.7.1.).hwp"

    assert make_source_id(name) == make_source_id(unicodedata.normalize("NFD", name))
    assert make_source_id(name) != make_source_id("1-0-2다른규정.hwp")


def test_compute_source_hash_detects_text_change():
    """청크 텍스트가 바뀌면 원문 해시도 바뀌어야 합니다. (건너뛰기 오판 방지)"""

    chunks = _sample_chunks()
    changed = _sample_chunks()
    changed[1]["text"] = "제15조(휴학) 학생은 휴학할 수 없다."

    assert compute_source_hash(chunks) == compute_source_hash(_sample_chunks())
    assert compute_source_hash(chunks) != compute_source_hash(changed)


def test_regulation_collection_name():
    """규정 컬렉션명이 FAQ와 분리돼 있어야 합니다."""

    assert REGULATION_COLLECTION_NAME == "kmu_regulations"


# ===== 실제 코퍼스 =====

@pytest.mark.skipif(not REGULATIONS_DIR.is_dir(), reason="규정 코퍼스가 없습니다.")
def test_list_regulation_files_finds_corpus():
    """코퍼스 디렉터리에서 HWP 파일을 찾아야 합니다."""

    files = list_regulation_files()

    assert files
    assert all(path.suffix.lower() == ".hwp" for path in files)


@pytest.mark.skipif(not REGULATIONS_DIR.is_dir(), reason="규정 코퍼스가 없습니다.")
@pytest.mark.asyncio
async def test_prepare_regulation_chunks_on_real_document():
    """실제 HWP 1건이 청크 → 포인트까지 변환돼야 합니다.

    임베딩 모델이 등록돼 있지 않아도 여기까지는 실제 파일로 검증할 수 있습니다.
    (벡터는 더미로 대체)
    """

    files = [path for path in list_regulation_files() if path.name.startswith("1-0-1")]
    if not files:
        pytest.skip("1-0-1 문서를 찾을 수 없습니다.")

    path = files[0]
    chunks = await prepare_regulation_chunks(path)
    if not chunks:
        pytest.skip("HWP 추출 도구(hwp5txt)를 사용할 수 없습니다.")

    points = build_regulation_points(
        chunks=chunks,
        vectors=[[0.0] * 8 for _ in chunks],
        document_id=1,
        file_name=path.name,
    )

    assert len(points) == len(chunks)
    # 문서메타 청크가 항상 첫 번째여야 합니다. (리포트 6.1절 파일명 시행일)
    assert points[0]["payload"]["section_type"] == "meta"
    assert points[0]["payload"]["effective_date"] == "2026-07-01"
    assert all(point["payload"]["doc_id"] == "1-0-1" for point in points)
    assert all(point["payload"]["content"] for point in points)

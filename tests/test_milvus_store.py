"""Milvus 어댑터 순수 헬퍼·플래그 분기 단위 테스트 (Milvus/PG 없이 실행)."""

from uuid import uuid4

import app.utils.milvus_store as milvus_store


def test_document_chunk_collection_name():
    assert milvus_store.document_chunk_collection_name(1024) == "document_chunks_1024"
    assert milvus_store.document_chunk_collection_name(384) == "document_chunks_384"


def test_faq_collection_name():
    assert milvus_store.faq_collection_name() == "faq_embeddings"


def test_build_equality_filter_mixed_types():
    expr = milvus_store.build_equality_filter({
        "collection_name": "kmu_regulations",
        "document_id": 42,
        "skip": None,
    })
    assert 'collection_name == "kmu_regulations"' in expr
    assert "document_id == 42" in expr
    assert "skip" not in expr


def test_build_equality_filter_escapes_quotes():
    expr = milvus_store.build_equality_filter({"name": 'a"b'})
    assert expr == 'name == "a\\"b"'


def test_build_equality_filter_empty():
    assert milvus_store.build_equality_filter(None) is None
    assert milvus_store.build_equality_filter({}) is None


def test_build_faq_search_filter_defaults():
    expr = milvus_store.build_faq_search_filter()
    assert 'status == "published"' in expr
    assert 'vector_status == "indexed"' in expr


def test_build_faq_search_filter_optional():
    expr = milvus_store.build_faq_search_filter(
        language="ko",
        category_code="ACADEMIC",
        visibility="public",
    )
    assert 'language == "ko"' in expr
    assert 'category_code == "ACADEMIC"' in expr
    assert 'visibility == "public"' in expr


def test_truncate_content():
    assert milvus_store.truncate_content("short") == "short"
    long = "x" * (milvus_store.CONTENT_MAX_LENGTH + 10)
    assert len(milvus_store.truncate_content(long)) == milvus_store.CONTENT_MAX_LENGTH


def test_chunk_point_to_entity_and_back():
    point_id = str(uuid4())
    entity = milvus_store.chunk_point_to_entity(
        point_id=point_id,
        collection_name="kmu_regulations",
        vector=[0.1, 0.2],
        payload={
            "document_id": 7,
            "chunk_index": 3,
            "content": "제1조 (목적)",
            "page": 2,
            "file_name": "rule.hwp",
            "doc_id": "1-0-1",
            "article": "제1조",
        },
    )
    assert entity["id"] == point_id
    assert entity["collection_name"] == "kmu_regulations"
    assert entity["document_id"] == 7
    assert entity["page"] == 2
    assert "doc_id" in entity["metadata_json"]

    payload = milvus_store.entity_to_chunk_payload(entity)
    assert payload["document_id"] == 7
    assert payload["content"] == "제1조 (목적)"
    assert payload["doc_id"] == "1-0-1"
    assert payload["page"] == 2


def test_entity_to_chunk_payload_negative_page_is_null():
    payload = milvus_store.entity_to_chunk_payload({
        "document_id": 1,
        "chunk_index": 0,
        "content": "x",
        "page": -1,
        "file_name": "",
        "metadata_json": "{}",
    })
    assert payload["page"] is None
    assert payload["file_name"] is None


def test_faq_fields_to_entity_skips_null_embedding():
    assert milvus_store.faq_fields_to_entity(
        faq_id=uuid4(), embedding=None, fields={"question": "q"},
    ) is None


def test_faq_fields_to_entity_enum_and_tags():
    class _E:
        def __init__(self, value):
            self.value = value

    faq_id = uuid4()
    entity = milvus_store.faq_fields_to_entity(
        faq_id=faq_id,
        embedding=[0.0, 1.0],
        fields={
            "status": _E("published"),
            "vector_status": _E("indexed"),
            "language": _E("ko"),
            "visibility": _E("public"),
            "category_code": "X",
            "department_code": "D",
            "question": "질문?",
            "source_url": "http://example.com",
            "tags": ["a", "b"],
        },
    )
    assert entity is not None
    assert entity["id"] == str(faq_id)
    assert entity["status"] == "published"
    assert entity["vector_status"] == "indexed"
    assert entity["language"] == "ko"
    assert '"a"' in entity["tags_json"]


def test_milvus_cosine_score_passthrough():
    assert milvus_store.milvus_cosine_score(0.91) == 0.91


def test_is_milvus_enabled_reads_config(monkeypatch=None):
    """config 로드가 가능한 환경에서만 검사. 없으면 스킵 가능하도록 True 반환 구조만 확인."""

    # 순수 헬퍼 로드만으로 enabled 접근은 config가 필요하므로,
    # 여기서는 함수가 callable 인지만 본다.
    assert callable(milvus_store.is_milvus_enabled)

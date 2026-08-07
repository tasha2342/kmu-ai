"""Milvus dense 벡터 저장소 어댑터.

`config.milvus.enabled=True`일 때 FAQ/문서의 dense ANN을 담당한다.
어휘(FTS)·원문 권위는 Postgres에 두고, 이 모듈은 벡터와 검색에 필요한 scalar만 다룬다.

pymilvus는 동기 API라 `asyncio.to_thread`로 감싼다.
컬렉션명·필터 표현식·entity 변환은 순수 함수로 두어 단위 테스트한다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional
from uuid import UUID

try:
    from app.utils.logger import get_logger
    try:
        logger = get_logger("milvus_store", log_dir="logs")
    except OSError:
        logger = get_logger("milvus_store")
except Exception:
    # 의존성 미설치 샌드박스에서도 순수 헬퍼 단위 테스트가 import 되게 한다
    import logging
    logger = logging.getLogger("milvus_store")


FAQ_MILVUS_COLLECTION = "faq_embeddings"
"""FAQ dense 벡터 컬렉션 이름 (PG 테이블명과 동일)"""

MILVUS_ALIAS = "kmu_ai"
"""pymilvus 연결 alias (다른 라이브러리 default와 겹치지 않게)"""

HNSW_INDEX_PARAMS = {
    "index_type": "HNSW",
    "metric_type": "COSINE",
    "params": {"M": 16, "efConstruction": 256},
}
SEARCH_PARAMS = {"metric_type": "COSINE", "params": {"ef": 64}}

CONTENT_MAX_LENGTH = 65535
"""Milvus VARCHAR 상한. 초과 본문은 잘라서 넣는다 (어휘 원천은 PG)."""


def _get_config():
    """앱 config를 지연 로드한다 (순수 헬퍼 단위 테스트가 toml 등 없이 돌게)."""

    from app.config import config
    return config


# ===== 순수 헬퍼 (테스트 가능) =====


def document_chunk_collection_name(vector_size: int) -> str:
    """차원별 문서 청크 Milvus 컬렉션 이름."""

    return f"document_chunks_{vector_size}"


def faq_collection_name() -> str:
    """FAQ Milvus 컬렉션 이름."""

    return FAQ_MILVUS_COLLECTION


def milvus_escape_str(value: str) -> str:
    """Milvus boolean expr 문자열 리터럴용 이스케이프."""

    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_equality_filter(filter_conditions: Optional[dict[str, Any]]) -> Optional[str]:
    """동등 비교 필터를 Milvus expr로 만든다.

    Args:
        filter_conditions: 필드명 → 스칼라 값 (str/int/bool)

    Returns:
        결합된 expr 또는 조건이 없으면 None
    """

    if not filter_conditions:
        return None

    parts: list[str] = []
    for key, value in filter_conditions.items():
        if value is None:
            continue
        if isinstance(value, bool):
            parts.append(f"{key} == {str(value).lower()}")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parts.append(f"{key} == {value}")
        else:
            parts.append(f'{key} == "{milvus_escape_str(str(value))}"')
    if not parts:
        return None
    return " and ".join(parts)


def build_faq_search_filter(
    *,
    language: Optional[str] = None,
    category_code: Optional[str] = None,
    visibility: Optional[str] = None,
) -> str:
    """FAQ 검색용 고정 필터 + 선택 필터 expr."""

    parts = [
        'status == "published"',
        'vector_status == "indexed"',
    ]
    if language:
        parts.append(f'language == "{milvus_escape_str(language)}"')
    if category_code:
        parts.append(f'category_code == "{milvus_escape_str(category_code)}"')
    if visibility:
        parts.append(f'visibility == "{milvus_escape_str(visibility)}"')
    return " and ".join(parts)


def truncate_content(content: Optional[str]) -> str:
    """Milvus VARCHAR 한도에 맞게 본문을 자른다."""

    text = content or ""
    if len(text) <= CONTENT_MAX_LENGTH:
        return text
    return text[:CONTENT_MAX_LENGTH]


def chunk_point_to_entity(
    *,
    point_id: str,
    collection_name: str,
    vector: list[float],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """VectorStore 포인트 dict를 Milvus upsert entity로 변환한다."""

    meta = {
        key: value
        for key, value in payload.items()
        if key not in ("document_id", "chunk_index", "content", "page", "file_name")
    }
    return {
        "id": str(point_id),
        "collection_name": collection_name,
        "document_id": int(payload.get("document_id") or 0),
        "chunk_index": int(payload.get("chunk_index") or 0),
        "content": truncate_content(payload.get("content")),
        "page": int(payload["page"]) if payload.get("page") is not None else -1,
        "file_name": (payload.get("file_name") or "")[:512],
        "metadata_json": json.dumps(meta, ensure_ascii=False, default=str),
        "embedding": list(vector),
    }


def entity_to_chunk_payload(entity: dict[str, Any]) -> dict[str, Any]:
    """Milvus hit entity/fields → VectorStore payload 형태."""

    try:
        meta = json.loads(entity.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}

    page = entity.get("page")
    if page is not None and int(page) < 0:
        page = None

    file_name = entity.get("file_name") or None
    payload = dict(meta)
    payload.update({
        "document_id": entity.get("document_id"),
        "chunk_index": entity.get("chunk_index"),
        "content": entity.get("content") or "",
        "page": page,
        "file_name": file_name,
    })
    return payload


def faq_fields_to_entity(
    *,
    faq_id: UUID | str,
    embedding: Optional[list[float]],
    fields: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """FAQ upsert용 entity. embedding이 없으면 None (Milvus에는 넣지 않음)."""

    if embedding is None:
        return None

    def _enum_value(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    tags = fields.get("tags") or []
    if not isinstance(tags, list):
        tags = list(tags) if tags else []

    return {
        "id": str(faq_id),
        "status": _enum_value(fields.get("status") or "draft"),
        "vector_status": _enum_value(fields.get("vector_status") or "pending"),
        "language": _enum_value(fields.get("language") or "ko"),
        "category_code": fields.get("category_code") or "",
        "visibility": _enum_value(fields.get("visibility") or "public"),
        "department_code": fields.get("department_code") or "",
        "question": truncate_content(fields.get("question") or ""),
        "source_url": truncate_content(fields.get("source_url") or ""),
        "tags_json": json.dumps(tags, ensure_ascii=False, default=str),
        "embedding": list(embedding),
    }


def milvus_cosine_score(distance: float) -> float:
    """Milvus COSINE 검색의 distance 필드를 pgvector와 같은 유사도 점수로 맞춘다.

    Milvus COSINE metric은 hit.distance에 cosine similarity(클수록 유사)를 넣는다.
    pgvector 경로의 score=`1 - cosine_distance`와 동일한 스케일이다.
    """

    return float(distance)


def is_milvus_enabled() -> bool:
    """컷오버 플래그."""

    milvus = getattr(_get_config(), "milvus", None)
    return bool(milvus and milvus.enabled)


# ===== Milvus 클라이언트 =====


class MilvusStore:
    """pymilvus 동기 클라이언트를 감싼 저장소."""

    def __init__(self):
        self._connected = False
        self._loaded: set[str] = set()

    def _connect(self):
        if self._connected:
            return

        from pymilvus import connections

        cfg = _get_config()
        kwargs: dict[str, Any] = {
            "alias": MILVUS_ALIAS,
            "host": cfg.milvus.host,
            "port": str(cfg.milvus.port),
            "db_name": cfg.milvus.db_name or "default",
        }
        if cfg.milvus.user:
            kwargs["user"] = cfg.milvus.user
            kwargs["password"] = cfg.milvus.password or ""

        connections.connect(**kwargs)
        self._connected = True
        logger.info(
            "Milvus에 연결했습니다. "
            f"(host={cfg.milvus.host}, port={cfg.milvus.port}, db={cfg.milvus.db_name})"
        )

    def _get_collection(self, name: str):
        from pymilvus import Collection

        self._connect()
        collection = Collection(name, using=MILVUS_ALIAS)
        if name not in self._loaded:
            collection.load()
            self._loaded.add(name)
        return collection

    def ensure_document_collection(self, vector_size: int):
        """문서 청크 컬렉션이 없으면 스키마·인덱스를 만든다."""

        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            utility,
        )

        self._connect()
        name = document_chunk_collection_name(vector_size)
        if utility.has_collection(name, using=MILVUS_ALIAS):
            self._get_collection(name)
            return name

        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="collection_name", dtype=DataType.VARCHAR, max_length=255),
            FieldSchema(name="document_id", dtype=DataType.INT64),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=CONTENT_MAX_LENGTH),
            FieldSchema(name="page", dtype=DataType.INT64),
            FieldSchema(name="file_name", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="metadata_json", dtype=DataType.VARCHAR, max_length=CONTENT_MAX_LENGTH),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=vector_size),
        ]
        schema = CollectionSchema(fields, description=f"document chunks dim={vector_size}")
        collection = Collection(name, schema, using=MILVUS_ALIAS)
        collection.create_index("embedding", HNSW_INDEX_PARAMS)
        # 필터 필드 보조 인덱스
        for field_name in ("collection_name", "document_id"):
            try:
                collection.create_index(field_name, {"index_type": "INVERTED"})
            except Exception:
                # 예전 서버는 INVERTED를 모를 수 있다. ANN만 있어도 동작한다.
                logger.debug(f"스칼라 인덱스 생략: {name}.{field_name}")
        collection.load()
        self._loaded.add(name)
        logger.info(f"Milvus 문서 컬렉션을 생성했습니다. (name={name}, dim={vector_size})")
        return name

    def ensure_faq_collection(self, vector_size: int):
        """FAQ 컬렉션이 없으면 스키마·인덱스를 만든다."""

        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            utility,
        )

        self._connect()
        name = faq_collection_name()
        if utility.has_collection(name, using=MILVUS_ALIAS):
            self._get_collection(name)
            return name

        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="status", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="vector_status", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="language", dtype=DataType.VARCHAR, max_length=16),
            FieldSchema(name="category_code", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="visibility", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="department_code", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="question", dtype=DataType.VARCHAR, max_length=CONTENT_MAX_LENGTH),
            FieldSchema(name="source_url", dtype=DataType.VARCHAR, max_length=CONTENT_MAX_LENGTH),
            FieldSchema(name="tags_json", dtype=DataType.VARCHAR, max_length=CONTENT_MAX_LENGTH),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=vector_size),
        ]
        schema = CollectionSchema(fields, description="FAQ embeddings")
        collection = Collection(name, schema, using=MILVUS_ALIAS)
        collection.create_index("embedding", HNSW_INDEX_PARAMS)
        for field_name in ("status", "vector_status", "language", "category_code", "visibility"):
            try:
                collection.create_index(field_name, {"index_type": "INVERTED"})
            except Exception:
                logger.debug(f"스칼라 인덱스 생략: {name}.{field_name}")
        collection.load()
        self._loaded.add(name)
        logger.info(f"Milvus FAQ 컬렉션을 생성했습니다. (name={name}, dim={vector_size})")
        return name

    def upsert_document_chunks(
        self,
        vector_size: int,
        collection_name: str,
        points: list[dict[str, Any]],
    ):
        if not points:
            return

        self.ensure_document_collection(vector_size)
        milvus_name = document_chunk_collection_name(vector_size)
        collection = self._get_collection(milvus_name)

        entities = [
            chunk_point_to_entity(
                point_id=str(point.get("id")),
                collection_name=collection_name,
                vector=point.get("vector") or [],
                payload=point.get("payload") or {},
            )
            for point in points
        ]
        collection.upsert(entities)
        collection.flush()

    def delete_document_chunks_by_ids(self, vector_size: int, ids: list[str]):
        if not ids:
            return
        from pymilvus import utility

        self._connect()
        name = document_chunk_collection_name(vector_size)
        if not utility.has_collection(name, using=MILVUS_ALIAS):
            return
        collection = self._get_collection(name)
        quoted = ", ".join(f'"{milvus_escape_str(i)}"' for i in ids)
        collection.delete(f"id in [{quoted}]")
        collection.flush()

    def delete_document_chunks_by_filter(
        self,
        vector_size: int,
        *,
        collection_name: str,
        document_id: Optional[int] = None,
    ):
        from pymilvus import utility

        self._connect()
        name = document_chunk_collection_name(vector_size)
        if not utility.has_collection(name, using=MILVUS_ALIAS):
            return

        parts = [f'collection_name == "{milvus_escape_str(collection_name)}"']
        if document_id is not None:
            parts.append(f"document_id == {int(document_id)}")
        expr = " and ".join(parts)
        collection = self._get_collection(name)
        collection.delete(expr)
        collection.flush()

    def delete_all_document_chunks_for_logical_collection(self, collection_name: str):
        """모든 차원 테이블에서 logical collection_name 청크를 지운다."""

        import app.models.database as db_models

        for vector_size in db_models.DOCUMENT_CHUNK_MODELS:
            self.delete_document_chunks_by_filter(vector_size, collection_name=collection_name)

    def search_document_chunks(
        self,
        vector_size: int,
        collection_name: str,
        query_vector: list[float],
        limit: int = 5,
        score_threshold: Optional[float] = None,
        filter_conditions: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        from pymilvus import utility

        self._connect()
        name = document_chunk_collection_name(vector_size)
        if not utility.has_collection(name, using=MILVUS_ALIAS):
            return []

        collection = self._get_collection(name)
        filters = {"collection_name": collection_name}
        if filter_conditions:
            filters.update(filter_conditions)
        expr = build_equality_filter(filters)

        output_fields = [
            "collection_name",
            "document_id",
            "chunk_index",
            "content",
            "page",
            "file_name",
            "metadata_json",
        ]
        hits = collection.search(
            data=[query_vector],
            anns_field="embedding",
            param=SEARCH_PARAMS,
            limit=limit,
            expr=expr,
            output_fields=output_fields,
        )

        results: list[dict[str, Any]] = []
        for hit in hits[0] if hits else []:
            score = milvus_cosine_score(hit.distance)
            if score_threshold is not None and score < score_threshold:
                continue
            entity = {field: hit.entity.get(field) for field in output_fields}
            results.append({
                "id": str(hit.id),
                "score": score,
                "payload": entity_to_chunk_payload(entity),
            })
        return results

    def count_document_chunks(self, vector_size: int, collection_name: Optional[str] = None) -> int:
        from pymilvus import utility

        self._connect()
        name = document_chunk_collection_name(vector_size)
        if not utility.has_collection(name, using=MILVUS_ALIAS):
            return 0
        collection = self._get_collection(name)
        if collection_name:
            result = collection.query(
                expr=f'collection_name == "{milvus_escape_str(collection_name)}"',
                output_fields=["id"],
            )
            return len(result)
        return collection.num_entities

    def upsert_faq(self, vector_size: int, entity: dict[str, Any]):
        self.ensure_faq_collection(vector_size)
        collection = self._get_collection(faq_collection_name())
        collection.upsert([entity])
        collection.flush()

    def delete_faq(self, faq_id: UUID | str):
        from pymilvus import utility

        self._connect()
        name = faq_collection_name()
        if not utility.has_collection(name, using=MILVUS_ALIAS):
            return
        collection = self._get_collection(name)
        collection.delete(f'id == "{milvus_escape_str(str(faq_id))}"')
        collection.flush()

    def search_faq(
        self,
        vector_size: int,
        query_vector: list[float],
        limit: int = 5,
        score_threshold: Optional[float] = None,
        language: Optional[str] = None,
        category_code: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        from pymilvus import utility

        self._connect()
        name = faq_collection_name()
        if not utility.has_collection(name, using=MILVUS_ALIAS):
            return []

        collection = self._get_collection(name)
        expr = build_faq_search_filter(
            language=language,
            category_code=category_code,
            visibility=visibility,
        )
        output_fields = [
            "status",
            "vector_status",
            "language",
            "category_code",
            "visibility",
            "department_code",
            "question",
            "source_url",
            "tags_json",
        ]
        hits = collection.search(
            data=[query_vector],
            anns_field="embedding",
            param=SEARCH_PARAMS,
            limit=limit,
            expr=expr,
            output_fields=output_fields,
        )

        results: list[dict[str, Any]] = []
        for hit in hits[0] if hits else []:
            score = milvus_cosine_score(hit.distance)
            if score_threshold is not None and score < score_threshold:
                continue
            try:
                tags = json.loads(hit.entity.get("tags_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                tags = []
            results.append({
                "faq_id": str(hit.id),
                "question": hit.entity.get("question") or "",
                "category_code": hit.entity.get("category_code") or None,
                "department_code": hit.entity.get("department_code") or None,
                "tags": tags if isinstance(tags, list) else [],
                "source_url": hit.entity.get("source_url") or None,
                "score": score,
            })
        return results

    def count_faq(self) -> int:
        from pymilvus import utility

        self._connect()
        name = faq_collection_name()
        if not utility.has_collection(name, using=MILVUS_ALIAS):
            return 0
        return self._get_collection(name).num_entities


_milvus_store: Optional[MilvusStore] = None


def get_milvus_store() -> MilvusStore:
    global _milvus_store
    if _milvus_store is None:
        _milvus_store = MilvusStore()
    return _milvus_store


async def ensure_document_collection_async(vector_size: int) -> str:
    store = get_milvus_store()
    return await asyncio.to_thread(store.ensure_document_collection, vector_size)


async def ensure_faq_collection_async(vector_size: int) -> str:
    store = get_milvus_store()
    return await asyncio.to_thread(store.ensure_faq_collection, vector_size)


async def upsert_document_chunks_async(
    vector_size: int,
    collection_name: str,
    points: list[dict[str, Any]],
):
    store = get_milvus_store()
    await asyncio.to_thread(store.upsert_document_chunks, vector_size, collection_name, points)


async def delete_document_collection_async(collection_name: str):
    store = get_milvus_store()
    await asyncio.to_thread(store.delete_all_document_chunks_for_logical_collection, collection_name)


async def delete_document_by_id_async(collection_name: str, document_id: int):
    import app.models.database as db_models

    store = get_milvus_store()

    def _delete():
        for vector_size in db_models.DOCUMENT_CHUNK_MODELS:
            store.delete_document_chunks_by_filter(
                vector_size,
                collection_name=collection_name,
                document_id=document_id,
            )

    await asyncio.to_thread(_delete)


async def search_document_chunks_async(
    vector_size: int,
    collection_name: str,
    query_vector: list[float],
    limit: int = 5,
    score_threshold: Optional[float] = None,
    filter_conditions: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    store = get_milvus_store()
    return await asyncio.to_thread(
        store.search_document_chunks,
        vector_size,
        collection_name,
        query_vector,
        limit,
        score_threshold,
        filter_conditions,
    )


async def upsert_faq_async(vector_size: int, entity: dict[str, Any]):
    store = get_milvus_store()
    await asyncio.to_thread(store.upsert_faq, vector_size, entity)


async def delete_faq_async(faq_id: UUID | str):
    store = get_milvus_store()
    await asyncio.to_thread(store.delete_faq, faq_id)


async def search_faq_async(
    vector_size: int,
    query_vector: list[float],
    limit: int = 5,
    score_threshold: Optional[float] = None,
    language: Optional[str] = None,
    category_code: Optional[str] = None,
    visibility: Optional[str] = None,
) -> list[dict[str, Any]]:
    store = get_milvus_store()
    return await asyncio.to_thread(
        store.search_faq,
        vector_size,
        query_vector,
        limit,
        score_threshold,
        language,
        category_code,
        visibility,
    )

import time
import hashlib

from uuid import UUID

from typing import Optional

from pydantic import BaseModel, Field

from app.config import config
from app.models.auth import TokenUserInfo
from app.models.enum import (
    ModelType,
    ModelStatus,
    FaqStatus,
    FaqVisibility,
    VectorStatus,
    Language,
)
from app.utils.database import DatabaseManager
from app.utils.logger import get_logger
from app.utils.litellm import create_embedding
from app.utils import milvus_store as milvus_store_mod
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.common as util


logger = get_logger("faq", log_dir="logs")


FAQ_COLLECTION_NAME = "kmu_faq_knowledge"
"""FAQ 지식베이스 논리 컬렉션명 (기능정의서 3. ERD의 `qdrant.collection` 명칭을 유지)

원문·메타는 PostgreSQL `faq_embeddings`에 둔다. dense ANN은 `config.milvus.enabled`가
False면 pgvector, True면 Milvus(`faq_embeddings` 컬렉션) + PG dual-write다.
이 이름은 임베딩 모델·차원을 기록하는 `collections` 레지스트리 행의 키로도 쓴다.
"""

class FaqKnowledgeBaseNotReady(Exception):
    """FAQ 지식베이스가 아직 만들어지지 않은 상태

    FAQ가 한 건도 색인되지 않으면 `collections` 레지스트리 행 자체가 없습니다.
    이건 장애가 아니라 "아직 넣을 FAQ가 없다"는 뜻이므로, 호출부가 모델 호출 오류와
    구분해서 다룰 수 있도록 별도 예외로 던집니다.
    """


EMBEDDING_VERSION = "v1"
"""임베딩 스키마 버전. 임베딩 텍스트 구성 방식을 바꾸면 올려서 전량 재색인을 유도합니다."""

DEFAULT_SCORE_THRESHOLD = config.chatbot.score_threshold
"""기본 유사도 임계값. 이보다 낮으면 미응답(`UnansweredReason.LOW_SCORE`)으로 처리합니다."""


class FaqSearchResult(BaseModel):
    """FAQ 유사도 검색 결과"""

    faq_id: UUID = Field(..., description="FAQ ID입니다.")
    question: str = Field(..., description="질문입니다.")
    answer: Optional[str] = Field(None, description="답변입니다. (DB 조회 시에만 채워집니다.)")
    category_code: Optional[str] = Field(None, description="카테고리 코드입니다.")
    department_code: Optional[str] = Field(None, description="담당 부서 코드입니다.")
    tags: list[str] = Field(default_factory=list, description="태그 목록입니다.")
    source_url: Optional[str] = Field(None, description="원문 URL입니다.")
    score: float = Field(..., description="코사인 유사도 점수입니다. (1 - 코사인 거리)")


class FaqSyncResult(BaseModel):
    """FAQ 색인 동기화 결과"""

    faq_id: UUID = Field(..., description="FAQ ID입니다.")
    vector_status: VectorStatus = Field(..., description="색인 결과 상태입니다.")
    skipped: bool = Field(False, description="원문 변경이 없어 건너뛰었는지 여부입니다.")
    error_message: Optional[str] = Field(None, description="오류 메시지입니다.")


def build_embedding_text(question: str, aliases: Optional[list[str]] = None) -> str:
    """임베딩 대상 텍스트를 구성합니다.

    ERD상 `embedding_text`는 `faq_item.question`이지만, 유사 질문(`question_aliases_json`)을
    함께 이어 붙여야 "수강신청 언제야" 같은 구어체 질의가 대표 질문에 걸립니다.
    구성 방식을 바꿀 때는 `EMBEDDING_VERSION`을 올려 전량 재색인이 되도록 합니다.

    Args:
        question (str): 대표 질문
        aliases (Optional[list[str]]): 유사 질문 목록

    Returns:
        str: 임베딩 대상 텍스트
    """

    parts = [question.strip()]
    for alias in aliases or []:
        alias = (alias or "").strip()
        if alias and alias not in parts:
            parts.append(alias)
    return "\n".join(parts)

def compute_text_hash(text: str) -> str:
    """임베딩 텍스트의 SHA-256 해시를 계산합니다.

    Args:
        text (str): 해시를 계산할 텍스트

    Returns:
        str: 64자리 16진수 해시
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def get_faq_collection(db_manager: DatabaseManager) -> Optional[db_items.Collection]:
    """FAQ 지식베이스 레지스트리 정보를 조회합니다.

    어떤 임베딩 모델·차원으로 색인 중인지를 일반 컬렉션과 같은 레코드(`collections`)로 관리합니다.
    한 곳에서만 관리해야 모델을 바꿨을 때 색인과 검색이 어긋나지 않기 때문입니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저

    Returns:
        Optional[db_items.Collection]: 컬렉션 정보 또는 None
    """

    query = (db_models.Collection.select()
             .where(db_models.Collection.name == FAQ_COLLECTION_NAME))
    return await db_manager.select_item(query)

async def get_embedding_model(
    db_manager: DatabaseManager,
    collection: db_items.Collection,
) -> Optional[db_items.Model]:
    """컬렉션에 설정된 임베딩 모델을 조회합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        collection (db_items.Collection): 컬렉션 정보

    Returns:
        Optional[db_items.Model]: 실행 중인 임베딩 모델 또는 None
    """

    query = (db_models.Model.select()
             .where(db_models.Model.name == collection.embedding_model)
             .where(db_models.Model.model_type == ModelType.EMBEDDING.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    return await db_manager.select_item(query)

async def embed_texts(
    model: db_items.Model,
    user_info: TokenUserInfo,
    texts: list[str],
) -> list[list[float]]:
    """텍스트 목록을 임베딩합니다.

    Args:
        model (db_items.Model): 임베딩 모델 정보
        user_info (TokenUserInfo): 사용자 정보
        texts (list[str]): 임베딩할 텍스트 목록

    Returns:
        list[list[float]]: 임베딩 벡터 목록

    Raises:
        ValueError: 모델이 반환한 차원이 `chatbot.embedding_dim`과 다른 경우
    """

    if not texts:
        return []

    response = await create_embedding(model, user_info, texts)
    vectors = [item["embedding"] for item in response.data]

    # 차원이 다르면 pgvector INSERT 단계에서 뒤늦게 터진다. 여기서 먼저 막는다.
    expected = db_models.FAQ_EMBEDDING_DIM
    if vectors and len(vectors[0]) != expected:
        raise ValueError(
            f"임베딩 차원이 설정과 다릅니다. (model={model.name}, actual={len(vectors[0])}, expected={expected}) "
            "configs/config.yaml의 chatbot.embedding_dim을 모델에 맞추고 전량 재색인해야 합니다."
        )
    return vectors

async def ensure_faq_collection(
    db_manager: DatabaseManager,
    embedding_model_name: str,
    user_info: TokenUserInfo,
) -> tuple[Optional[db_items.Collection], Optional[str]]:
    """FAQ 지식베이스 레지스트리를 준비합니다. (없으면 생성)

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        embedding_model_name (str): 사용할 임베딩 모델명
        user_info (TokenUserInfo): 사용자 정보

    Returns:
        tuple[Optional[db_items.Collection], Optional[str]]: (컬렉션 정보, 오류 메시지)
    """

    collection = await get_faq_collection(db_manager)
    if collection:
        return collection, None

    query = (db_models.Model.select()
             .where(db_models.Model.name == embedding_model_name)
             .where(db_models.Model.model_type == ModelType.EMBEDDING.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return None, "등록되지 않은 임베딩 모델이거나 실행중이 아닙니다."

    # 실제 차원이 설정과 맞는지 여기서 확인해 둔다 (맞지 않으면 색인이 전부 실패한다)
    try:
        await embed_texts(model, user_info, ["test"])
    except ValueError as exc:
        return None, str(exc)
    except Exception:
        logger.exception("임베딩 모델 테스트 중 오류가 발생했습니다.")
        return None, "임베딩 모델 테스트 중 오류가 발생했습니다."

    query = db_models.Collection.insert(
        name=FAQ_COLLECTION_NAME,
        user_name=user_info.username,
        embedding_model=embedding_model_name,
        vector_size=db_models.FAQ_EMBEDDING_DIM,
        description="계명대 FAQ 지식베이스 (pgvector 유사도 검색용)",
        is_system=True,
    )
    await db_manager.execute_query(query)

    logger.info(
        "FAQ 지식베이스 레지스트리가 생성되었습니다. "
        f"(vector_size={db_models.FAQ_EMBEDDING_DIM}, model={embedding_model_name})"
    )
    return await get_faq_collection(db_manager), None


async def sync_faq_item(
    db_manager: DatabaseManager,
    collection: db_items.Collection,
    model: db_items.Model,
    user_info: TokenUserInfo,
    faq: db_items.FaqItem,
    force: bool = False,
) -> FaqSyncResult:
    """FAQ 한 건을 임베딩해 `faq_embeddings`에 저장합니다.

    `PUBLISHED`가 아닌 FAQ는 색인 대상에서 제외하고 기존 벡터를 삭제합니다.
    (임시 저장·보관 중인 답변이 챗봇 응답 근거로 잡히면 안 되기 때문)

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        collection (db_items.Collection): FAQ 컬렉션 정보
        model (db_items.Model): 임베딩 모델 정보
        user_info (TokenUserInfo): 사용자 정보
        faq (db_items.FaqItem): 색인할 FAQ 항목
        force (bool): 해시가 같아도 강제로 재색인할지 여부 (Default: False)

    Returns:
        FaqSyncResult: 색인 결과
    """

    query = (db_models.FaqEmbedding.select()
             .where(db_models.FaqEmbedding.faq_id == faq.id))
    index: Optional[db_items.FaqEmbedding] = await db_manager.select_item(query)

    # 공개 상태가 아니면 색인 대상에서 제외
    if faq.status != FaqStatus.PUBLISHED:
        await delete_faq_vectors(db_manager, faq.id)
        return FaqSyncResult(faq_id=faq.id, vector_status=VectorStatus.PENDING, skipped=True)

    embedding_text = build_embedding_text(faq.question, faq.question_aliases_json)
    text_hash = compute_text_hash(embedding_text)

    # 원문·모델·임베딩 버전이 모두 그대로면 재색인 불필요
    if (not force and index
            and index.embedding_text_hash == text_hash
            and index.embedding_model == collection.embedding_model
            and index.embedding_version == EMBEDDING_VERSION
            and index.vector_status == VectorStatus.INDEXED):
        return FaqSyncResult(faq_id=faq.id, vector_status=VectorStatus.INDEXED, skipped=True)

    query = (db_models.FaqCategory.select()
             .where(db_models.FaqCategory.id == faq.category_id))
    category: Optional[db_items.FaqCategory] = await db_manager.select_item(query)

    now = util.get_now()
    fields = {
        "embedding_text": embedding_text,
        "embedding_text_hash": text_hash,
        "embedding_model": collection.embedding_model,
        "embedding_version": EMBEDDING_VERSION,
        "category_code": category.category_code if category else None,
        "department_code": faq.department_code,
        "language": faq.language,
        "visibility": faq.visibility,
        "status": faq.status,
        "tags": faq.tags_json or [],
        "source_url": faq.source_url,
        "question": faq.question,
        "version": faq.version,
        "updated_at": now,
    }

    try:
        vectors = await embed_texts(model, user_info, [embedding_text])
    except Exception as exc:
        logger.exception(f"FAQ 임베딩 중 오류가 발생했습니다. (faq_id={faq.id})")
        await _upsert_embedding_row(
            db_manager, index, faq.id,
            {**fields, "embedding": None, "vector_status": VectorStatus.FAILED, "indexed_at": None},
        )
        return FaqSyncResult(
            faq_id=faq.id,
            vector_status=VectorStatus.FAILED,
            error_message=str(exc)[:1024],
        )

    await _upsert_embedding_row(
        db_manager, index, faq.id,
        {**fields, "embedding": vectors[0], "vector_status": VectorStatus.INDEXED, "indexed_at": now},
    )
    return FaqSyncResult(faq_id=faq.id, vector_status=VectorStatus.INDEXED)

async def _upsert_embedding_row(
    db_manager: DatabaseManager,
    index: Optional[db_items.FaqEmbedding],
    faq_id: UUID,
    fields: dict,
):
    """`faq_embeddings` 행을 생성하거나 갱신합니다. milvus.enabled면 Milvus에도 dual-write."""

    if index:
        query = (db_models.FaqEmbedding.update(**fields)
                 .where(db_models.FaqEmbedding.id == index.id))
    else:
        query = db_models.FaqEmbedding.insert(faq_id=faq_id, **fields)
    await db_manager.execute_query(query)

    if milvus_store_mod.is_milvus_enabled():
        embedding = fields.get("embedding")
        entity = milvus_store_mod.faq_fields_to_entity(
            faq_id=faq_id, embedding=embedding, fields=fields,
        )
        if entity is None:
            # 색인 실패 등으로 벡터가 없으면 Milvus에서 제거해 PG와 맞춘다
            await milvus_store_mod.delete_faq_async(faq_id)
        else:
            await milvus_store_mod.upsert_faq_async(db_models.FAQ_EMBEDDING_DIM, entity)


async def delete_faq_vectors(db_manager: DatabaseManager, faq_id: UUID):
    """FAQ의 임베딩 레코드를 삭제합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        faq_id (UUID): FAQ ID
    """

    query = (db_models.FaqEmbedding.delete()
             .where(db_models.FaqEmbedding.faq_id == faq_id))
    await db_manager.execute_query(query)

    if milvus_store_mod.is_milvus_enabled():
        await milvus_store_mod.delete_faq_async(faq_id)


async def mark_faq_stale(db_manager: DatabaseManager, faq_id: UUID):
    """FAQ 원문이 수정되었음을 임베딩 레코드에 표시합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        faq_id (UUID): FAQ ID
    """

    query = (db_models.FaqEmbedding.update(
        vector_status=VectorStatus.STALE,
        updated_at=util.get_now(),
    ).where(db_models.FaqEmbedding.faq_id == faq_id))
    await db_manager.execute_query(query)

    # stale은 검색 필터에서 빠지므로 Milvus에서도 제거 (재색인 시 다시 올라온다)
    if milvus_store_mod.is_milvus_enabled():
        await milvus_store_mod.delete_faq_async(faq_id)


async def search_faq(
    db_manager: DatabaseManager,
    user_info: TokenUserInfo,
    query_text: str,
    top_k: int = config.chatbot.top_k,
    score_threshold: Optional[float] = DEFAULT_SCORE_THRESHOLD,
    language: Optional[Language] = None,
    category_code: Optional[str] = None,
    visibility: Optional[FaqVisibility] = None,
    with_answer: bool = True,
) -> tuple[list[FaqSearchResult], int]:
    """FAQ 지식베이스에서 유사 질문을 검색합니다. (기능정의서 2. 챗봇 데이터 확인 - FAQ 유사도 검색)

    `milvus.enabled=False`면 pgvector 코사인 거리(`<=>`) HNSW,
    True면 Milvus COSINE ANN. 유사도는 cosine similarity로 환산합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        user_info (TokenUserInfo): 사용자 정보
        query_text (str): 사용자 질문
        top_k (int): 반환할 결과 수
        score_threshold (Optional[float]): 최소 유사도 점수
        language (Optional[Language]): 언어 필터
        category_code (Optional[str]): 카테고리 코드 필터
        visibility (Optional[FaqVisibility]): 공개 범위 필터
        with_answer (bool): 답변 본문을 DB에서 함께 조회할지 여부 (Default: True)

    Returns:
        tuple[list[FaqSearchResult], int]: (검색 결과, 검색 지연 시간 ms)

    Raises:
        FaqKnowledgeBaseNotReady: 색인된 FAQ가 없어 지식베이스가 아직 만들어지지 않은 경우
        ValueError: 임베딩 모델을 사용할 수 없는 경우
    """

    started = time.time()

    collection = await get_faq_collection(db_manager)
    if not collection:
        raise FaqKnowledgeBaseNotReady("FAQ 지식베이스가 준비되지 않았습니다. (색인된 FAQ 없음)")

    model = await get_embedding_model(db_manager, collection)
    if not model:
        raise ValueError("FAQ 지식베이스의 임베딩 모델을 사용할 수 없습니다.")

    vectors = await embed_texts(model, user_info, [query_text])

    results: list[FaqSearchResult] = []

    if milvus_store_mod.is_milvus_enabled():
        hits = await milvus_store_mod.search_faq_async(
            db_models.FAQ_EMBEDDING_DIM,
            vectors[0],
            limit=top_k,
            score_threshold=score_threshold,
            language=language.value if language else None,
            category_code=category_code,
            visibility=visibility.value if visibility else None,
        )
        for hit in hits:
            results.append(FaqSearchResult(
                faq_id=UUID(str(hit["faq_id"])),
                question=hit["question"],
                category_code=hit.get("category_code"),
                department_code=hit.get("department_code"),
                tags=hit.get("tags") or [],
                source_url=hit.get("source_url"),
                score=hit["score"],
            ))
    else:
        distance = db_models.FaqEmbedding.embedding.cosine_distance(vectors[0])
        query = (db_models.FaqEmbedding
                 .select(db_models.FaqEmbedding, distance.alias("distance"))
                 # 임시 저장·보관 FAQ와 색인 실패분이 검색되지 않도록 항상 고정한다
                 .where(db_models.FaqEmbedding.status == FaqStatus.PUBLISHED.value)
                 .where(db_models.FaqEmbedding.vector_status == VectorStatus.INDEXED.value)
                 .where(db_models.FaqEmbedding.embedding.is_null(False))
                 .order_by(distance)
                 .limit(top_k))
        if language:
            query = query.where(db_models.FaqEmbedding.language == language.value)
        if category_code:
            query = query.where(db_models.FaqEmbedding.category_code == category_code)
        if visibility:
            query = query.where(db_models.FaqEmbedding.visibility == visibility.value)

        # 벡터 검색은 임베딩 CPU 부하와 겹치면 기본 5초를 넘기기 쉽다.
        rows = await db_manager.execute_query(query, timeout=30.0)

        for row in rows:
            score = 1 - row.distance
            if score_threshold is not None and score < score_threshold:
                continue
            results.append(FaqSearchResult(
                faq_id=row.faq_id,
                question=row.question,
                category_code=row.category_code,
                department_code=row.department_code,
                tags=row.tags or [],
                source_url=row.source_url,
                score=score,
            ))

    # 답변 본문은 색인 테이블에 두지 않는다(장문·잦은 수정). 필요할 때만 원본에서 최신본을 읽는다.
    if with_answer and results:
        answer_query = (db_models.FaqItem.select()
                        .where(db_models.FaqItem.id.in_([r.faq_id for r in results])))
        faqs: list[db_items.FaqItem] = await db_manager.select_items(answer_query)
        answer_map = {faq.id: faq.answer for faq in faqs}
        for result in results:
            result.answer = answer_map.get(result.faq_id)

    return results, int((time.time() - started) * 1000)

from logging import Logger

from datetime import datetime
from typing import Optional

from peewee import fn

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Path, Query, UploadFile
from botocore.exceptions import ClientError

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
)
from app.payloads.v1.rag import (
    SyncRagItemsPayload,
    DeleteRagItemsPayload,
    UpdateRagActivePayload,
    UpdateRagItemPayload,
)
from app.responses.base import BaseListResponse
from app.responses.v1.rag import (
    RagItemInfo,
    RagItemDetailResponse,
    RagSyncResponse,
    RagSyncResultItem,
    RagDeleteResponse,
    RagDeleteResultItem,
    RagLogItem,
)
from app.responses.v1.document import UploadDocumentResponse
from app.responses.exception import (
    BadRequestResponse,
    NotFoundResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import (
    BadRequestError,
    NotFoundError,
)
from app.models.enum import (
    DocumentStatus,
    RagEmbeddingStatus,
    SourceType,
    IngestionStatus,
    VectorStatus,
    FaqStatus,
)
from app.scheduler.jobs.chatbot import (
    select_faq_targets,
    get_running_job,
    create_ingestion_job,
    finish_ingestion_job,
    run_faq_ingestion,
    run_regulation_ingestion,
)
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.s3 import get_s3_manager, S3Manager
from app.utils.vector_store import get_vector_store_manager, VectorStoreManager
from app.utils.graphrag import get_graphrag_service, GraphRAGService
from app.utils.faq_service import FAQ_COLLECTION_NAME
from app.utils.regulation_ingest import REGULATION_COLLECTION_NAME
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


# 관리형 지식베이스 메타 (화면 표시용)
KNOWN_KB_META: dict[str, dict] = {
    FAQ_COLLECTION_NAME: {
        "display_name": "FAQ 지식베이스",
        "display_name_en": "FAQ Knowledge",
        "bot_label": "학생 봇",
        "bot_label_en": "Student Bot",
        "source_type": SourceType.FAQ,
    },
    REGULATION_COLLECTION_NAME: {
        "display_name": "대학 규정집",
        "display_name_en": "Uni Regulations",
        "bot_label": "학생 봇",
        "bot_label_en": "Student Bot",
        "source_type": SourceType.REGULATION,
    },
}

STATUS_LABELS = {
    RagEmbeddingStatus.SYNCING: "동기화 중",
    RagEmbeddingStatus.SUCCESS: "동기화 완료",
    RagEmbeddingStatus.ERROR: "에러",
}

SOURCE_TYPE_BY_COLLECTION = {
    name: meta["source_type"] for name, meta in KNOWN_KB_META.items()
}


def _parse_month_filter(value: Optional[str]) -> Optional[tuple[datetime, datetime]]:
    """`YYYY.MM` 또는 `YYYY-MM` 형식의 월 필터를 시작/종료 datetime으로 변환합니다."""

    if not value:
        return None
    normalized = value.strip().replace("/", "-").replace(".", "-")
    parts = [p for p in normalized.split("-") if p]
    if len(parts) < 2:
        raise ValueError("날짜 필터는 YYYY.MM 형식이어야 합니다.")
    year, month = int(parts[0]), int(parts[1])
    if month < 1 or month > 12:
        raise ValueError("날짜 필터의 월이 올바르지 않습니다.")
    start = datetime(year, month, 1, tzinfo=util.get_now().tzinfo)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=start.tzinfo)
    else:
        end = datetime(year, month + 1, 1, tzinfo=start.tzinfo)
    return start, end


def _resolve_meta(collection: db_items.Collection) -> dict:
    """컬렉션의 화면 표시 메타를 반환합니다."""

    known = KNOWN_KB_META.get(collection.name, {})
    description = collection.description or ""
    display_name = known.get("display_name") or description or collection.name
    return {
        "display_name": display_name,
        "display_name_en": known.get("display_name_en"),
        "bot_label": known.get("bot_label", "일반"),
        "bot_label_en": known.get("bot_label_en"),
        "source_type": known.get("source_type"),
    }


async def _faq_counts(db_manager: DatabaseManager) -> tuple[int, int]:
    """FAQ 문서(항목) 수와 색인된 임베딩 수를 반환합니다."""

    faq_count_query = (db_models.FaqItem
                       .select(fn.COUNT(db_models.FaqItem.id).alias("count"))
                       .where(db_models.FaqItem.status == FaqStatus.PUBLISHED.value))
    faq_rows = await db_manager.execute_query(faq_count_query)
    document_count = faq_rows[0].count if faq_rows else 0

    emb_count_query = (db_models.FaqEmbedding
                       .select(fn.COUNT(db_models.FaqEmbedding.id).alias("count"))
                       .where(db_models.FaqEmbedding.vector_status == VectorStatus.INDEXED.value))
    emb_rows = await db_manager.execute_query(emb_count_query)
    chunk_count = emb_rows[0].count if emb_rows else 0
    return document_count, chunk_count


async def _document_status_counts(
    db_manager: DatabaseManager,
    collection_name: str,
) -> dict[str, int]:
    """컬렉션 문서 상태별 건수를 반환합니다."""

    query = (db_models.Document
             .select(
                 db_models.Document.status,
                 fn.COUNT(db_models.Document.id).alias("count")
             )
             .where(db_models.Document.collection_name == collection_name)
             .group_by(db_models.Document.status))
    rows = await db_manager.execute_query(query)
    return {str(row.status): row.count for row in rows}


async def _latest_ingestion_job(
    db_manager: DatabaseManager,
    source_type: SourceType,
) -> Optional[db_items.IngestionJob]:
    """원천 유형별 최근 수집 작업을 반환합니다."""

    query = (db_models.IngestionJob
             .select()
             .where(db_models.IngestionJob.source_type == source_type.value)
             .order_by(db_models.IngestionJob.started_at.desc())
             .limit(1))
    return await db_manager.select_item(query)


async def _derive_embedding_status(
    db_manager: DatabaseManager,
    collection: db_items.Collection,
    source_type: Optional[SourceType],
) -> tuple[RagEmbeddingStatus, Optional[str], Optional[db_items.IngestionJob]]:
    """임베딩/동기화 상태와 최근 오류, 최근 작업을 산출합니다."""

    last_job = None
    if source_type in (SourceType.FAQ, SourceType.REGULATION):
        last_job = await _latest_ingestion_job(db_manager, source_type)
        if last_job and last_job.status == IngestionStatus.RUNNING.value:
            return RagEmbeddingStatus.SYNCING, None, last_job
        if last_job and last_job.status == IngestionStatus.FAILED.value:
            return RagEmbeddingStatus.ERROR, last_job.error_message, last_job

    if collection.name == FAQ_COLLECTION_NAME:
        failed_query = (db_models.FaqEmbedding
                        .select(fn.COUNT(db_models.FaqEmbedding.id).alias("count"))
                        .where(db_models.FaqEmbedding.vector_status == VectorStatus.FAILED.value))
        failed_rows = await db_manager.execute_query(failed_query)
        if failed_rows and failed_rows[0].count > 0:
            return RagEmbeddingStatus.ERROR, f"FAQ 색인 실패 {failed_rows[0].count}건", last_job
        pending_query = (db_models.FaqEmbedding
                         .select(fn.COUNT(db_models.FaqEmbedding.id).alias("count"))
                         .where(db_models.FaqEmbedding.vector_status.in_([
                             VectorStatus.PENDING.value,
                             VectorStatus.STALE.value,
                         ])))
        pending_rows = await db_manager.execute_query(pending_query)
        if pending_rows and pending_rows[0].count > 0:
            return RagEmbeddingStatus.SYNCING, None, last_job
        return RagEmbeddingStatus.SUCCESS, None, last_job

    status_counts = await _document_status_counts(db_manager, collection.name)
    processing = status_counts.get(DocumentStatus.PROCESSING.value, 0)
    errors = status_counts.get(DocumentStatus.ERROR.value, 0)
    if processing > 0:
        return RagEmbeddingStatus.SYNCING, None, last_job
    if errors > 0:
        # 최근 오류 문서 메시지
        err_query = (db_models.Document
                     .select()
                     .where(db_models.Document.collection_name == collection.name)
                     .where(db_models.Document.status == DocumentStatus.ERROR.value)
                     .order_by(db_models.Document.updated_at.desc())
                     .limit(1))
        err_doc: Optional[db_items.Document] = await db_manager.select_item(err_query)
        message = err_doc.error_message if err_doc else f"오류 문서 {errors}건"
        return RagEmbeddingStatus.ERROR, message, last_job
    return RagEmbeddingStatus.SUCCESS, None, last_job


async def _build_item(
    db_manager: DatabaseManager,
    collection: db_items.Collection,
    doc_stats: Optional[dict] = None,
) -> RagItemInfo:
    """목록용 지식베이스 항목을 구성합니다."""

    meta = _resolve_meta(collection)
    source_type = meta["source_type"]

    if collection.name == FAQ_COLLECTION_NAME:
        document_count, chunk_count = await _faq_counts(db_manager)
    else:
        stats = doc_stats or {}
        document_count = stats.get("document_count", 0)
        chunk_count = stats.get("chunk_count", 0)

    embedding_status, _, last_job = await _derive_embedding_status(
        db_manager, collection, source_type
    )

    last_synced_at = None
    if last_job and last_job.ended_at:
        last_synced_at = util.serialize_datetime(last_job.ended_at)
    elif last_job and last_job.started_at:
        last_synced_at = util.serialize_datetime(last_job.started_at)
    else:
        last_synced_at = util.serialize_datetime(collection.updated_at)

    return RagItemInfo(
        name=collection.name,
        display_name=meta["display_name"],
        display_name_en=meta["display_name_en"],
        bot_label=meta["bot_label"],
        bot_label_en=meta["bot_label_en"],
        source_type=source_type,
        document_count=document_count,
        chunk_count=int(chunk_count or 0),
        embedding_status=embedding_status,
        embedding_status_label=STATUS_LABELS[embedding_status],
        last_synced_at=last_synced_at,
        is_active=getattr(collection, "is_active", True),
        vector_db="pgvector",
        embedding_model=collection.embedding_model,
        vector_size=collection.vector_size,
        description=collection.description,
        is_system=collection.is_system,
        chunk_size=int(getattr(collection, "chunk_size", None) or 1000),
        chunk_overlap=int(getattr(collection, "chunk_overlap", None) or 100),
        top_k=int(getattr(collection, "top_k", None) or 5),
        similarity_threshold=float(
            getattr(collection, "similarity_threshold", None)
            if getattr(collection, "similarity_threshold", None) is not None
            else 0.35
        ),
        created_at=util.serialize_datetime(collection.created_at),
        updated_at=util.serialize_datetime(collection.updated_at),
    )


async def _build_detail(
    db_manager: DatabaseManager,
    collection: db_items.Collection,
) -> RagItemDetailResponse:
    """상세 패널용 응답을 구성합니다."""

    item = await _build_item(db_manager, collection)
    meta = _resolve_meta(collection)
    embedding_status, recent_error, last_job = await _derive_embedding_status(
        db_manager, collection, meta["source_type"]
    )

    processing_count = 0
    error_count = 0
    if collection.name != FAQ_COLLECTION_NAME:
        status_counts = await _document_status_counts(db_manager, collection.name)
        processing_count = status_counts.get(DocumentStatus.PROCESSING.value, 0)
        error_count = status_counts.get(DocumentStatus.ERROR.value, 0)

    return RagItemDetailResponse(
        **item.model_dump(exclude={"embedding_status", "embedding_status_label"}),
        embedding_status=embedding_status,
        embedding_status_label=STATUS_LABELS[embedding_status],
        recent_error=recent_error,
        processing_document_count=processing_count,
        error_document_count=error_count,
        last_job_id=last_job.id if last_job else None,
        last_job_status=(
            IngestionStatus(last_job.status)
            if last_job and not isinstance(last_job.status, IngestionStatus)
            else (last_job.status if last_job else None)
        ),
    )


async def _delete_collection_data(
    collection_name: str,
    db_manager: DatabaseManager,
    s3_manager: S3Manager,
    vector_store: VectorStoreManager,
    graphrag_service: GraphRAGService,
    logger: Logger,
) -> None:
    """컬렉션과 연관 데이터를 삭제합니다. (collection 엔드포인트와 동일 로직)"""

    query = (db_models.Document.select()
             .where(db_models.Document.collection_name == collection_name))
    documents: list[db_items.Document] = await db_manager.select_items(query)

    for doc in documents:
        try:
            s3_manager.delete_file(doc.file_path)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code != "404":
                logger.exception(f"파일 삭제 중 오류가 발생했습니다. (file_path={doc.file_path})")

    await db_manager.execute_query(
        db_models.Document.delete().where(db_models.Document.collection_name == collection_name)
    )

    if await vector_store.collection_exists_async(collection_name):
        await vector_store.delete_collection_async(collection_name)

    graphrag_service.delete_collection_graph(collection_name)

    await db_manager.execute_query(
        db_models.Collection.delete().where(db_models.Collection.name == collection_name)
    )


async def _start_ingestion(
    source_type: SourceType,
    force: bool,
    only_stale: bool,
    background_tasks: BackgroundTasks,
    db_manager: DatabaseManager,
    user_info: TokenUserInfo,
    logger: Logger,
) -> RagSyncResultItem:
    """기존 ingestion 로직으로 재색인을 시작합니다."""

    running_job = await get_running_job(db_manager, source_type)
    if running_job:
        return RagSyncResultItem(
            name="",
            success=False,
            message="이미 진행 중인 수집 작업이 있습니다. 작업이 끝난 뒤 다시 시도해주세요.",
            job_id=running_job.id,
        )

    job_id = await create_ingestion_job(db_manager, source_type)

    async def _process_ingestion():
        try:
            if source_type == SourceType.REGULATION:
                await run_regulation_ingestion(
                    db_manager=db_manager,
                    job_id=job_id,
                    user_info=user_info,
                    force=force or not only_stale,
                    request_logger=logger,
                )
                return

            faqs = await select_faq_targets(db_manager, only_stale=only_stale)
            await run_faq_ingestion(
                db_manager=db_manager,
                job_id=job_id,
                user_info=user_info,
                faqs=faqs,
                force=force,
                request_logger=logger,
            )
        except Exception as exc:
            logger.exception(f"수집 작업 처리 중 오류가 발생했습니다. (job_id={job_id})")
            await finish_ingestion_job(
                db_manager=db_manager,
                job_id=job_id,
                status=IngestionStatus.FAILED,
                error_message=f"수집 작업 처리 중 오류가 발생했습니다. ({exc})",
            )

    background_tasks.add_task(_process_ingestion)

    start_messages = {
        SourceType.FAQ: "FAQ 재색인 작업이 시작되었습니다.",
        SourceType.REGULATION: "학칙·규정 재색인 작업이 시작되었습니다.",
    }
    return RagSyncResultItem(
        name="",
        success=True,
        message=start_messages.get(source_type, "재색인 작업이 시작되었습니다."),
        job_id=job_id,
    )


@router.get("/items", summary="RAG 지식베이스 목록 조회",
    description=(
        "RAG Management 화면용 지식베이스 목록을 조회합니다.  \n"
        "- 검색어·임베딩 상태·활성 여부·월 단위 날짜로 필터링합니다.  \n"
        "- 문서/청크 수, 동기화 상태, 최근 동기화 시각을 포함합니다."
    ),
    responses={
        200: {"description": "지식베이스 목록을 반환합니다.", "model": BaseListResponse[RagItemInfo]},
        400: {"description": "잘못된 요청입니다.", "model": BadRequestError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def list_rag_items(
    page: int = Query(1, ge=1, description="페이지 번호입니다.", examples=[1]),
    count: int = Query(25, ge=0, le=100, description="페이지당 항목 수입니다. (0이면 전체)", examples=[25]),
    search: Optional[str] = Query(None, description="이름·설명 검색어입니다.", examples=["규정"]),
    status: Optional[RagEmbeddingStatus] = Query(
        None,
        description="임베딩/동기화 상태로 필터링합니다.",
        examples=list(RagEmbeddingStatus)
    ),
    is_active: Optional[bool] = Query(None, description="활성 여부로 필터링합니다."),
    date: Optional[str] = Query(
        None,
        description="최근 수정/동기화 월 필터입니다. (`YYYY.MM`)",
        examples=["2024.07"]
    ),
    include_system: bool = Query(True, description="시스템 지식베이스 포함 여부입니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    try:
        month_range = _parse_month_filter(date)
    except ValueError as exc:
        return BadRequestResponse(message=str(exc), target=f"date={date}")

    query = db_models.Collection.select()
    if not include_system:
        query = query.where(db_models.Collection.is_system == False)
    if is_active is not None:
        query = query.where(db_models.Collection.is_active == is_active)
    if search:
        query = query.where(
            (db_models.Collection.name.contains(search)) |
            (db_models.Collection.description.contains(search))
        )
    if month_range:
        start, end = month_range
        query = query.where(
            (db_models.Collection.updated_at >= start) &
            (db_models.Collection.updated_at < end)
        )

    query = query.order_by(db_models.Collection.updated_at.desc())
    collections: list[db_items.Collection] = await db_manager.select_items(query)

    # 문서 통계 (FAQ 제외)
    stats_query = (db_models.Document.select(
        db_models.Document.collection_name,
        fn.COUNT(db_models.Document.id).alias("document_count"),
        fn.COALESCE(fn.SUM(db_models.Document.chunk_count), 0).alias("chunk_count")
    ).where(db_models.Document.status == DocumentStatus.COMPLETED.value)
     .group_by(db_models.Document.collection_name))
    stats_rows = await db_manager.execute_query(stats_query)
    stats_map = {
        row.collection_name: {
            "document_count": row.document_count,
            "chunk_count": row.chunk_count,
        }
        for row in stats_rows
    }

    items: list[RagItemInfo] = []
    for collection in collections:
        item = await _build_item(db_manager, collection, stats_map.get(collection.name))
        if status and item.embedding_status != status:
            continue
        items.append(item)

    total_count = len(items)
    if count > 0:
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
        offset = (page - 1) * count
        items = items[offset:offset + count]
    else:
        total_pages = 1

    logger.debug(f"RAG 지식베이스 목록 조회 (total={total_count}, page={page})")
    return BaseListResponse[RagItemInfo](
        total_pages=total_pages,
        total_count=total_count,
        items=items,
    )


@router.get("/items/{name}", summary="RAG 지식베이스 상세 조회",
    description="상세 패널용 지식베이스 통계·최근 오류·최근 작업을 조회합니다.",
    responses={
        200: {"description": "지식베이스 상세를 반환합니다.", "model": RagItemDetailResponse},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_rag_item(
    name: str = Path(..., description="지식베이스(컬렉션) 이름입니다.", examples=["kmu_regulations"]),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
):
    query = db_models.Collection.select().where(db_models.Collection.name == name)
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="지식베이스를 찾을 수 없습니다.",
            target=f"name={name}"
        )
    return await _build_detail(db_manager, collection)


@router.patch("/items/{name}", summary="RAG 지식베이스 수정",
    description="설명·활성 여부·청킹/검색 파라미터(chunk_size, top_k, similarity_threshold)를 수정합니다.",
    responses={
        200: {"description": "수정된 지식베이스 상세를 반환합니다.", "model": RagItemDetailResponse},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_rag_item(
    payload: UpdateRagItemPayload,
    name: str = Path(..., description="지식베이스(컬렉션) 이름입니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    query = db_models.Collection.select().where(db_models.Collection.name == name)
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="지식베이스를 찾을 수 없습니다.",
            target=f"name={name}"
        )

    update_data = {}
    payload_set = payload.model_dump(exclude_unset=True)
    for key in (
        "description",
        "is_active",
        "chunk_size",
        "chunk_overlap",
        "top_k",
        "similarity_threshold",
    ):
        if key in payload_set and payload_set[key] is not None:
            update_data[key] = payload_set[key]
        elif key == "description" and "description" in payload_set:
            update_data[key] = payload_set[key]

    if update_data:
        update_data["updated_at"] = util.get_now()
        await db_manager.execute_query(
            db_models.Collection.update(**update_data).where(db_models.Collection.name == name)
        )
        collection = await db_manager.select_item(query)
        logger.info(f"RAG 지식베이스가 수정되었습니다. (name={name})")

    return await _build_detail(db_manager, collection)


@router.patch("/items/{name}/active", summary="RAG 지식베이스 활성 토글",
    description="검색(RAG) 활성 여부를 토글합니다.",
    responses={
        200: {"description": "수정된 지식베이스 상세를 반환합니다.", "model": RagItemDetailResponse},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_rag_item_active(
    payload: UpdateRagActivePayload,
    name: str = Path(..., description="지식베이스(컬렉션) 이름입니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    query = db_models.Collection.select().where(db_models.Collection.name == name)
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="지식베이스를 찾을 수 없습니다.",
            target=f"name={name}"
        )

    await db_manager.execute_query(
        db_models.Collection.update(
            is_active=payload.is_active,
            updated_at=util.get_now(),
        ).where(db_models.Collection.name == name)
    )
    collection = await db_manager.select_item(query)
    logger.info(f"RAG 지식베이스 활성 상태가 변경되었습니다. (name={name}, is_active={payload.is_active})")
    return await _build_detail(db_manager, collection)


@router.post("/items/{name}/upload", summary="RAG 지식베이스 문서 업로드",
    description=(
        "선택한 지식베이스(컬렉션)에 문서를 업로드하고 임베딩합니다.  \n"
        "`chunk_size`/`chunk_overlap`을 생략하면 컬렉션에 저장된 기본값을 사용합니다.  \n"
        "FAQ 지식베이스(`kmu_faq_knowledge`)는 FAQ 관리 API로만 등록할 수 있습니다."
    ),
    responses={
        200: {"description": "업로드 결과를 반환합니다.", "model": UploadDocumentResponse},
        400: {"description": "잘못된 요청입니다.", "model": BadRequestError},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def upload_rag_document(
    background_tasks: BackgroundTasks,
    name: str = Path(..., description="지식베이스(컬렉션) 이름입니다."),
    file: UploadFile = File(..., description="업로드할 문서 파일입니다."),
    background: bool = Form(
        True,
        description="백그라운드 처리 여부입니다. 기본값 true(즉시 응답).",
    ),
    chunk_size: Optional[int] = Form(
        None,
        ge=100,
        description="청크 크기. 생략 시 컬렉션 설정을 사용합니다.",
    ),
    chunk_overlap: Optional[int] = Form(
        None,
        ge=0,
        description="청크 오버랩. 생략 시 컬렉션 설정을 사용합니다.",
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    s3_manager: S3Manager = Depends(get_s3_manager),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    if name == FAQ_COLLECTION_NAME:
        return BadRequestResponse(
            message="FAQ 지식베이스는 문서 업로드를 지원하지 않습니다. /v1/faq API를 사용하세요.",
            target=f"name={name}",
        )

    query = db_models.Collection.select().where(db_models.Collection.name == name)
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="지식베이스를 찾을 수 없습니다.",
            target=f"name={name}",
        )

    from app.api.v1.endpoints.document import upload_document

    return await upload_document(
        background_tasks=background_tasks,
        file=file,
        background=background,
        collection_name=name,
        chunk_size=chunk_size if chunk_size is not None else int(getattr(collection, "chunk_size", 1000) or 1000),
        chunk_overlap=chunk_overlap if chunk_overlap is not None else int(getattr(collection, "chunk_overlap", 100) or 100),
        file_data=None,
        db_manager=db_manager,
        s3_manager=s3_manager,
        vector_store=vector_store,
        user_info=user_info,
        logger=logger,
    )


@router.post("/sync", summary="RAG 지식베이스 동기화",
    description=(
        "선택한 지식베이스를 재색인합니다.  \n"
        "- `kmu_faq_knowledge`: FAQ 재색인  \n"
        "- `kmu_regulations`: 학칙·규정 재색인  \n"
        "- 그 외 업로드 문서 컬렉션은 현재 지원하지 않습니다."
    ),
    responses={
        200: {"description": "동기화 요청 결과를 반환합니다.", "model": RagSyncResponse},
        400: {"description": "잘못된 요청입니다.", "model": BadRequestError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def sync_rag_items(
    payload: SyncRagItemsPayload,
    background_tasks: BackgroundTasks,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    results: list[RagSyncResultItem] = []

    for name in payload.names:
        source_type = SOURCE_TYPE_BY_COLLECTION.get(name)
        if not source_type:
            results.append(RagSyncResultItem(
                name=name,
                success=False,
                message="업로드 문서 컬렉션은 원천 연계 재색인을 지원하지 않습니다. 문서는 /v1/document/upload 로 추가하세요.",
                job_id=None,
            ))
            continue

        query = db_models.Collection.select().where(db_models.Collection.name == name)
        collection = await db_manager.select_item(query)
        # FAQ는 컬렉션 행이 아직 없을 수 있음 (최초 색인 전)
        if not collection and name != FAQ_COLLECTION_NAME:
            results.append(RagSyncResultItem(
                name=name,
                success=False,
                message="지식베이스를 찾을 수 없습니다.",
                job_id=None,
            ))
            continue

        started = await _start_ingestion(
            source_type=source_type,
            force=payload.force,
            only_stale=payload.only_stale,
            background_tasks=background_tasks,
            db_manager=db_manager,
            user_info=user_info,
            logger=logger,
        )
        results.append(RagSyncResultItem(
            name=name,
            success=started.success,
            message=started.message,
            job_id=started.job_id,
        ))

    success_count = sum(1 for item in results if item.success)
    return RagSyncResponse(
        message=f"{success_count}/{len(results)}건 동기화를 시작했습니다.",
        results=results,
    )


@router.delete("/items", summary="RAG 지식베이스 일괄 삭제",
    description=(
        "선택한 지식베이스(컬렉션)를 삭제합니다.  \n"
        "문서 파일·벡터·그래프 데이터가 함께 삭제됩니다.  \n"
        "시스템 관리형 KB(`kmu_faq_knowledge`, `kmu_regulations`)는 보호되어 삭제되지 않습니다."
    ),
    responses={
        200: {"description": "삭제 결과를 반환합니다.", "model": RagDeleteResponse},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_rag_items(
    payload: DeleteRagItemsPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    s3_manager: S3Manager = Depends(get_s3_manager),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    results: list[RagDeleteResultItem] = []
    protected = set(KNOWN_KB_META.keys())

    for name in payload.names:
        if name in protected:
            results.append(RagDeleteResultItem(
                name=name,
                success=False,
                message="시스템 관리형 지식베이스는 삭제할 수 없습니다.",
            ))
            continue

        query = db_models.Collection.select().where(db_models.Collection.name == name)
        collection = await db_manager.select_item(query)
        if not collection:
            results.append(RagDeleteResultItem(
                name=name,
                success=False,
                message="지식베이스를 찾을 수 없습니다.",
            ))
            continue

        try:
            await _delete_collection_data(
                collection_name=name,
                db_manager=db_manager,
                s3_manager=s3_manager,
                vector_store=vector_store,
                graphrag_service=graphrag_service,
                logger=logger,
            )
            results.append(RagDeleteResultItem(
                name=name,
                success=True,
                message="삭제되었습니다.",
            ))
            logger.info(f"RAG 지식베이스가 삭제되었습니다. (name={name})")
        except Exception as exc:
            logger.exception(f"지식베이스 삭제 중 오류 (name={name})")
            results.append(RagDeleteResultItem(
                name=name,
                success=False,
                message=f"삭제 중 오류가 발생했습니다. ({exc})",
            ))

    success_count = sum(1 for item in results if item.success)
    return RagDeleteResponse(
        message=f"{success_count}/{len(results)}건 삭제되었습니다.",
        results=results,
    )


@router.get("/items/{name}/logs", summary="RAG 지식베이스 로그 조회",
    description=(
        "지식베이스의 최근 동기화/오류 로그를 조회합니다.  \n"
        "관리형 KB는 수집 작업 이력, 문서 컬렉션은 오류 문서 기록을 반환합니다."
    ),
    responses={
        200: {"description": "로그 목록을 반환합니다.", "model": BaseListResponse[RagLogItem]},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_rag_item_logs(
    name: str = Path(..., description="지식베이스(컬렉션) 이름입니다."),
    page: int = Query(1, ge=1, description="페이지 번호입니다."),
    count: int = Query(20, ge=1, le=100, description="페이지당 항목 수입니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
):
    query = db_models.Collection.select().where(db_models.Collection.name == name)
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection and name != FAQ_COLLECTION_NAME:
        return NotFoundResponse(
            message="지식베이스를 찾을 수 없습니다.",
            target=f"name={name}"
        )

    logs: list[RagLogItem] = []
    source_type = SOURCE_TYPE_BY_COLLECTION.get(name)

    if source_type:
        job_query = (db_models.IngestionJob
                     .select()
                     .where(db_models.IngestionJob.source_type == source_type.value)
                     .order_by(db_models.IngestionJob.started_at.desc())
                     .limit(100))
        jobs: list[db_items.IngestionJob] = await db_manager.select_items(job_query)
        for job in jobs:
            logs.append(RagLogItem(
                id=str(job.id),
                event_type="ingestion_job",
                status=str(job.status),
                message=job.error_message or (
                    f"success={job.success_count}, failed={job.failed_count}, total={job.total_count}"
                ),
                created_at=util.serialize_datetime(job.started_at),
                meta={
                    "ended_at": util.serialize_datetime(job.ended_at),
                    "source_type": str(job.source_type),
                },
            ))
    else:
        doc_query = (db_models.Document
                     .select()
                     .where(db_models.Document.collection_name == name)
                     .where(db_models.Document.status == DocumentStatus.ERROR.value)
                     .order_by(db_models.Document.updated_at.desc())
                     .limit(100))
        docs: list[db_items.Document] = await db_manager.select_items(doc_query)
        for doc in docs:
            logs.append(RagLogItem(
                id=str(doc.id),
                event_type="document_error",
                status=str(doc.status),
                message=doc.error_message or doc.file_name,
                created_at=util.serialize_datetime(doc.updated_at),
                meta={"file_name": doc.file_name, "file_type": doc.file_type},
            ))

    total_count = len(logs)
    total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    offset = (page - 1) * count
    return BaseListResponse[RagLogItem](
        total_pages=total_pages,
        total_count=total_count,
        items=logs[offset:offset + count],
    )


@router.get("/export", summary="RAG 지식베이스 목록 내보내기",
    description="현재 필터 조건의 지식베이스 목록을 JSON으로 내보냅니다.",
    responses={
        200: {"description": "내보내기 목록을 반환합니다.", "model": BaseListResponse[RagItemInfo]},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def export_rag_items(
    search: Optional[str] = Query(None, description="이름·설명 검색어입니다."),
    status: Optional[RagEmbeddingStatus] = Query(None, description="임베딩 상태 필터입니다."),
    is_active: Optional[bool] = Query(None, description="활성 여부 필터입니다."),
    include_system: bool = Query(True, description="시스템 지식베이스 포함 여부입니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 페이지네이션 없이 전체 조회 재사용
    response = await list_rag_items(
        page=1,
        count=0,
        search=search,
        status=status,
        is_active=is_active,
        date=None,
        include_system=include_system,
        db_manager=db_manager,
        user_info=user_info,
        logger=logger,
    )
    return response

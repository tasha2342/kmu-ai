from logging import Logger

from uuid import UUID, uuid4

from typing import Any, Optional

from peewee import fn

from fastapi import APIRouter, Depends, Path, Query

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
)
from app.payloads.v1.faq import (
    CreateFaqCategoryPayload,
    UpdateFaqCategoryPayload,
    CreateFaqPayload,
    UpdateFaqPayload,
    SyncFaqPayload,
    SearchFaqPayload,
)
from app.responses.base import BaseMessageResponse, BaseListResponse
from app.responses.v1.faq import (
    FaqCategoryInfo,
    FaqCategoryListResponse,
    FaqInfo,
    FaqIndexInfo,
    FaqDetailResponse,
    FaqMutationResponse,
    FaqSyncResponse,
    FaqSearchResponse,
)
from app.responses.exception import (
    BadRequestResponse,
    NotFoundResponse,
    ConflictResponse,
    InternalServerErrorResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import (
    BadRequestError,
    NotFoundError,
    ConflictError,
)
from app.models.enum import FaqStatus, FaqVisibility, Language, VectorStatus
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.faq_service import (
    FaqSyncResult,
    build_embedding_text,
    compute_text_hash,
    ensure_faq_collection,
    get_embedding_model,
    sync_faq_item,
    delete_faq_vectors,
    mark_faq_stale,
    search_faq,
)
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


INDEX_UNAVAILABLE_MESSAGE = (
    "색인에 실패했습니다. FAQ는 정상 저장되었으며, 색인 동기화(/faq/sync)로 재시도할 수 있습니다."
)
"""벡터 색인 실패·임베딩 모델 장애 시 CRUD 응답에 담는 경고 메시지"""

FAQ_UPDATE_NON_NULLABLE_FIELDS = {
    "category_id",
    "question",
    "answer",
    "question_aliases_json",
    "tags_json",
    "visibility",
    "status",
    "language",
}
"""수정 요청에서 NULL로 비울 수 없는 필드 목록"""


def _enum_value(value: Any) -> Any:
    """Enum 값을 데이터베이스 저장용 원시 값으로 변환합니다.

    Args:
        value (Any): 변환할 값

    Returns:
        Any: Enum이면 `value` 속성, 아니면 원본 값
    """

    return value.value if hasattr(value, "value") else value

def _to_category_info(category: db_items.FaqCategory, faq_count: int = 0) -> FaqCategoryInfo:
    """카테고리 아이템을 응답 모델로 변환합니다.

    Args:
        category (db_items.FaqCategory): 카테고리 아이템
        faq_count (int): 카테고리에 속한 FAQ 개수 (Default: 0)

    Returns:
        FaqCategoryInfo: 카테고리 정보 응답 모델
    """

    return FaqCategoryInfo(
        id=category.id,
        parent_id=category.parent_id,
        category_name=category.category_name,
        category_code=category.category_code,
        department_code=category.department_code,
        display_order=category.display_order,
        is_active=category.is_active,
        faq_count=faq_count,
        created_at=util.serialize_datetime(category.created_at),
        updated_at=util.serialize_datetime(category.updated_at)
    )

def _to_faq_info(faq: db_items.FaqItem, category: Optional[db_items.FaqCategory] = None) -> FaqInfo:
    """FAQ 아이템을 응답 모델로 변환합니다.

    Args:
        faq (db_items.FaqItem): FAQ 아이템
        category (Optional[db_items.FaqCategory]): 소속 카테고리 아이템

    Returns:
        FaqInfo: FAQ 정보 응답 모델
    """

    return FaqInfo(
        id=faq.id,
        category_id=faq.category_id,
        category_code=category.category_code if category else None,
        category_name=category.category_name if category else None,
        question=faq.question,
        answer=faq.answer,
        question_aliases_json=faq.question_aliases_json or [],
        tags_json=faq.tags_json or [],
        source_url=faq.source_url,
        department_code=faq.department_code,
        visibility=faq.visibility,
        status=faq.status,
        language=faq.language,
        version=faq.version,
        created_at=util.serialize_datetime(faq.created_at),
        updated_at=util.serialize_datetime(faq.updated_at)
    )

def _to_index_info(index: "db_items.FaqEmbedding", faq: db_items.FaqItem) -> FaqIndexInfo:
    """색인 레코드를 응답 모델로 변환합니다.

    현재 질문·유사 질문으로 다시 계산한 해시가 색인된 해시와 다르면
    재색인이 필요한 상태(`is_stale`)로 표시합니다.

    Args:
        index (db_items.FaqEmbedding): 색인 레코드
        faq (db_items.FaqItem): FAQ 아이템

    Returns:
        FaqIndexInfo: 색인 상태 응답 모델
    """

    current_hash = compute_text_hash(build_embedding_text(faq.question, faq.question_aliases_json))
    is_stale = (index.embedding_text_hash != current_hash) or (index.vector_status == VectorStatus.STALE)

    return FaqIndexInfo(
        embedding_model=index.embedding_model,
        embedding_version=index.embedding_version,
        embedding_text_hash=index.embedding_text_hash,
        vector_status=index.vector_status,
        is_stale=is_stale,
        indexed_at=util.serialize_datetime(index.indexed_at)
    )

async def _get_faq_index(
    db_manager: DatabaseManager,
    faq_id: UUID,
) -> Optional["db_items.FaqEmbedding"]:
    """FAQ의 색인 레코드를 조회합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        faq_id (UUID): FAQ ID

    Returns:
        Optional[db_items.FaqEmbedding]: 색인 레코드 또는 None
    """

    query = (db_models.FaqEmbedding.select()
             .where(db_models.FaqEmbedding.faq_id == faq_id))
    return await db_manager.select_item(query)

async def _get_faq_by_id(db_manager: DatabaseManager, faq_id: UUID) -> Optional[db_items.FaqItem]:
    """FAQ를 ID로 조회합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        faq_id (UUID): FAQ ID

    Returns:
        Optional[db_items.FaqItem]: FAQ 아이템 또는 None
    """

    query = (db_models.FaqItem.select()
             .where(db_models.FaqItem.id == faq_id))
    return await db_manager.select_item(query)

async def _get_category_by_id(
    db_manager: DatabaseManager,
    category_id: UUID,
) -> Optional[db_items.FaqCategory]:
    """카테고리를 ID로 조회합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        category_id (UUID): 카테고리 ID

    Returns:
        Optional[db_items.FaqCategory]: 카테고리 아이템 또는 None
    """

    query = (db_models.FaqCategory.select()
             .where(db_models.FaqCategory.id == category_id))
    return await db_manager.select_item(query)

async def _try_sync_faq(
    db_manager: DatabaseManager,
    user_info: TokenUserInfo,
    faq: db_items.FaqItem,
    logger: Logger,
    force: bool = False,
) -> tuple[bool, Optional[str]]:
    """FAQ 한 건의 색인을 시도합니다. (실패해도 예외를 전파하지 않습니다.)

    임베딩 생성이나 벡터 색인이 실패하더라도 FAQ CRUD 자체를 실패시키면 안 됩니다.
    실패는 로그로 남기고 호출자에게 경고 메시지만 돌려줍니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        user_info (TokenUserInfo): 사용자 정보
        faq (db_items.FaqItem): 색인할 FAQ 아이템
        logger (Logger): 로거
        force (bool): 원문 변경이 없어도 강제로 재색인할지 여부 (Default: False)

    Returns:
        tuple[bool, Optional[str]]: (색인 완료 여부, 경고 메시지)
    """

    try:
        collection, error_message = await ensure_faq_collection(
            db_manager=db_manager,
            embedding_model_name=config.chatbot.embedding_model,
            user_info=user_info
        )
        if not collection:
            logger.warning(f"FAQ 지식베이스 컬렉션을 준비하지 못했습니다. (faq_id={faq.id}, reason={error_message})")
            return False, error_message or INDEX_UNAVAILABLE_MESSAGE

        model = await get_embedding_model(db_manager, collection)
        if not model:
            logger.warning(f"FAQ 임베딩 모델을 사용할 수 없습니다. (faq_id={faq.id}, model={collection.embedding_model})")
            return False, "FAQ 지식베이스의 임베딩 모델을 사용할 수 없습니다."

        result = await sync_faq_item(
            db_manager=db_manager,
            collection=collection,
            model=model,
            user_info=user_info,
            faq=faq,
            force=force
        )
    except Exception:
        logger.exception(f"FAQ 색인 중 오류가 발생했습니다. (faq_id={faq.id})")
        return False, INDEX_UNAVAILABLE_MESSAGE

    if result.vector_status == VectorStatus.FAILED:
        return False, result.error_message or INDEX_UNAVAILABLE_MESSAGE

    return result.vector_status == VectorStatus.INDEXED and not result.skipped, None


@router.get("/category/list", summary="FAQ 카테고리 목록 조회",
    description=(
        "FAQ 카테고리 목록을 조회합니다.  \n"
        "- 노출 순서(`display_order`) 오름차순으로 정렬됩니다.  \n"
        "- 상위 카테고리 ID(`parent_id`)를 포함하므로 계층 구조를 구성할 수 있습니다.  \n"
        "- 카테고리별 FAQ 개수를 함께 반환합니다."
    ),
    responses={
        200: {"description": "FAQ 카테고리 목록을 반환합니다.", "model": FaqCategoryListResponse},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_faq_categories(
    is_active: Optional[bool] = Query(
        None,
        description=(
            "사용 여부로 필터링합니다.  \n"
            "지정하지 않으면 전체를 조회합니다."
        ),
        examples=[True, False]
    ),
    parent_id: Optional[UUID] = Query(
        None,
        description="상위 카테고리 ID로 필터링합니다."
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 카테고리 목록 조회
    query = db_models.FaqCategory.select()
    if is_active is not None:
        query = query.where(db_models.FaqCategory.is_active == is_active)
    if parent_id is not None:
        query = query.where(db_models.FaqCategory.parent_id == parent_id)
    query = query.order_by(
        db_models.FaqCategory.display_order.asc(),
        db_models.FaqCategory.category_name.asc()
    )
    categories: list[db_items.FaqCategory] = await db_manager.select_items(query)

    # 카테고리별 FAQ 개수 조회
    count_query = (db_models.FaqItem.select(
        db_models.FaqItem.category_id,
        fn.COUNT(db_models.FaqItem.id).alias("faq_count")
    ).group_by(db_models.FaqItem.category_id))
    count_results = await db_manager.execute_query(count_query)
    count_map = {row.category_id: row.faq_count for row in count_results}

    items = [
        _to_category_info(category, count_map.get(category.id, 0))
        for category in categories
    ]

    return FaqCategoryListResponse(
        categories=items,
        total_count=len(items)
    )

@router.post("/category/create", summary="FAQ 카테고리 생성",
    description=(
        "FAQ 카테고리를 생성합니다.  \n"
        "- 카테고리 코드(`category_code`)는 전체에서 중복될 수 없습니다.  \n"
        "- `parent_id`를 지정하면 하위 카테고리로 생성됩니다."
    ),
    responses={
        200: {"description": "생성된 카테고리 정보를 반환합니다.", "model": FaqCategoryInfo},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found_parent": {
                            "summary": "등록되지 않은 상위 카테고리",
                            "value": {
                                "message": "등록되지 않은 상위 카테고리입니다.",
                                "target": "parent_id={parent_id}"
                            }
                        }
                    }
                }
            }
        },
        409: {
            "description": "이미 존재하는 항목입니다.",
            "model": ConflictError,
            "content": {
                "application/json": {
                    "examples": {
                        "conflict": {
                            "summary": "이미 존재하는 카테고리 코드",
                            "value": {
                                "message": "이미 존재하는 카테고리 코드입니다.",
                                "target": "category_code={category_code}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def create_faq_category(
    payload: CreateFaqCategoryPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 카테고리 코드 중복 확인
    query = (db_models.FaqCategory.select()
             .where(db_models.FaqCategory.category_code == payload.category_code))
    existing_category = await db_manager.select_item(query)
    if existing_category:
        return ConflictResponse(
            message="이미 존재하는 카테고리 코드입니다.",
            target=f"category_code={payload.category_code}"
        )

    # 상위 카테고리 확인
    if payload.parent_id:
        parent = await _get_category_by_id(db_manager, payload.parent_id)
        if not parent:
            return NotFoundResponse(
                message="등록되지 않은 상위 카테고리입니다.",
                target=f"parent_id={payload.parent_id}"
            )

    # 카테고리 생성
    category_id = uuid4()
    query = db_models.FaqCategory.insert(
        id=category_id,
        parent_id=payload.parent_id,
        category_name=payload.category_name,
        category_code=payload.category_code,
        department_code=payload.department_code,
        display_order=payload.display_order,
        is_active=payload.is_active
    )
    await db_manager.execute_query(query)

    logger.info(f"FAQ 카테고리가 생성되었습니다. (category_id={category_id}, category_code={payload.category_code})")

    category = await _get_category_by_id(db_manager, category_id)
    return _to_category_info(category)

@router.patch("/category/update/{category_id}", summary="FAQ 카테고리 수정",
    description=(
        "FAQ 카테고리 정보를 수정합니다.  \n"
        "- 요청에 포함된 항목만 수정됩니다.  \n"
        "- 카테고리 코드(`category_code`)는 전체에서 중복될 수 없습니다.  \n"
        "- 자기 자신을 상위 카테고리로 지정할 수 없습니다."
    ),
    responses={
        200: {"description": "수정된 카테고리 정보를 반환합니다.", "model": FaqCategoryInfo},
        400: {
            "description": "요청이 올바르지 않습니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "self_parent": {
                            "summary": "자기 자신을 상위 카테고리로 지정",
                            "value": {
                                "message": "자기 자신을 상위 카테고리로 지정할 수 없습니다.",
                                "target": "parent_id={parent_id}"
                            }
                        }
                    }
                }
            }
        },
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 카테고리",
                            "value": {
                                "message": "등록되지 않은 카테고리입니다.",
                                "target": "category_id={category_id}"
                            }
                        },
                        "not_found_parent": {
                            "summary": "등록되지 않은 상위 카테고리",
                            "value": {
                                "message": "등록되지 않은 상위 카테고리입니다.",
                                "target": "parent_id={parent_id}"
                            }
                        }
                    }
                }
            }
        },
        409: {
            "description": "이미 존재하는 항목입니다.",
            "model": ConflictError,
            "content": {
                "application/json": {
                    "examples": {
                        "conflict": {
                            "summary": "이미 존재하는 카테고리 코드",
                            "value": {
                                "message": "이미 존재하는 카테고리 코드입니다.",
                                "target": "category_code={category_code}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_faq_category(
    payload: UpdateFaqCategoryPayload,
    category_id: UUID = Path(
        ...,
        description="수정할 카테고리 ID입니다."
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 카테고리 조회
    category = await _get_category_by_id(db_manager, category_id)
    if not category:
        return NotFoundResponse(
            message="등록되지 않은 카테고리입니다.",
            target=f"category_id={category_id}"
        )

    update_values = payload.model_dump(exclude_unset=True)

    # 카테고리명·코드는 NULL로 비울 수 없음
    for field_name in ("category_name", "category_code", "display_order", "is_active"):
        if field_name in update_values and update_values[field_name] is None:
            update_values.pop(field_name)

    # 카테고리 코드 중복 확인
    if "category_code" in update_values and update_values["category_code"] != category.category_code:
        query = (db_models.FaqCategory.select()
                 .where(db_models.FaqCategory.category_code == update_values["category_code"])
                 .where(db_models.FaqCategory.id != category_id))
        duplicated = await db_manager.select_item(query)
        if duplicated:
            return ConflictResponse(
                message="이미 존재하는 카테고리 코드입니다.",
                target=f"category_code={update_values['category_code']}"
            )

    # 상위 카테고리 확인
    if update_values.get("parent_id"):
        if update_values["parent_id"] == category_id:
            return BadRequestResponse(
                message="자기 자신을 상위 카테고리로 지정할 수 없습니다.",
                target=f"parent_id={update_values['parent_id']}"
            )
        parent = await _get_category_by_id(db_manager, update_values["parent_id"])
        if not parent:
            return NotFoundResponse(
                message="등록되지 않은 상위 카테고리입니다.",
                target=f"parent_id={update_values['parent_id']}"
            )

    if update_values:
        update_values["updated_at"] = util.get_now()
        query = (db_models.FaqCategory.update(**update_values)
                 .where(db_models.FaqCategory.id == category_id))
        await db_manager.execute_query(query)
        category = await _get_category_by_id(db_manager, category_id)

    logger.info(f"FAQ 카테고리가 수정되었습니다. (category_id={category_id})")

    return _to_category_info(category)

@router.delete("/category/delete/{category_id}", summary="FAQ 카테고리 삭제",
    description=(
        "FAQ 카테고리를 삭제합니다.  \n"
        "- 하위 카테고리가 있으면 삭제할 수 없습니다.  \n"
        "- 소속된 FAQ가 있으면 삭제할 수 없습니다."
    ),
    responses={
        200: {"description": "카테고리가 삭제되었습니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 카테고리",
                            "value": {
                                "message": "등록되지 않은 카테고리입니다.",
                                "target": "category_id={category_id}"
                            }
                        }
                    }
                }
            }
        },
        409: {
            "description": "삭제할 수 없는 항목입니다.",
            "model": ConflictError,
            "content": {
                "application/json": {
                    "examples": {
                        "has_children": {
                            "summary": "하위 카테고리 존재",
                            "value": {
                                "message": "하위 카테고리가 있어 삭제할 수 없습니다.",
                                "target": "category_id={category_id}, child_count={child_count}"
                            }
                        },
                        "has_faqs": {
                            "summary": "소속 FAQ 존재",
                            "value": {
                                "message": "소속된 FAQ가 있어 삭제할 수 없습니다.",
                                "target": "category_id={category_id}, faq_count={faq_count}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_faq_category(
    category_id: UUID = Path(
        ...,
        description="삭제할 카테고리 ID입니다."
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 카테고리 조회
    category = await _get_category_by_id(db_manager, category_id)
    if not category:
        return NotFoundResponse(
            message="등록되지 않은 카테고리입니다.",
            target=f"category_id={category_id}"
        )

    # 하위 카테고리 확인
    query = (db_models.FaqCategory.select(fn.COUNT(db_models.FaqCategory.id).alias("count"))
             .where(db_models.FaqCategory.parent_id == category_id))
    result = await db_manager.execute_query(query)
    child_count = result[0].count if result else 0
    if child_count > 0:
        return ConflictResponse(
            message="하위 카테고리가 있어 삭제할 수 없습니다.",
            target=f"category_id={category_id}, child_count={child_count}"
        )

    # 소속 FAQ 확인
    query = (db_models.FaqItem.select(fn.COUNT(db_models.FaqItem.id).alias("count"))
             .where(db_models.FaqItem.category_id == category_id))
    result = await db_manager.execute_query(query)
    faq_count = result[0].count if result else 0
    if faq_count > 0:
        return ConflictResponse(
            message="소속된 FAQ가 있어 삭제할 수 없습니다.",
            target=f"category_id={category_id}, faq_count={faq_count}"
        )

    # 카테고리 삭제
    query = (db_models.FaqCategory.delete()
             .where(db_models.FaqCategory.id == category_id))
    await db_manager.execute_query(query)

    logger.info(f"FAQ 카테고리가 삭제되었습니다. (category_id={category_id})")

    return BaseMessageResponse(message="FAQ 카테고리가 삭제되었습니다.")


@router.get("/list", summary="FAQ 목록 조회",
    description=(
        "FAQ 목록을 페이징 처리하여 조회합니다.  \n"
        "- 카테고리·상태·공개 범위·언어·담당 부서로 필터링할 수 있습니다.  \n"
        "- `keyword`는 질문과 답변 본문에서 검색합니다.  \n"
        "- 생성일 최신순으로 정렬됩니다."
    ),
    responses={
        200: {"description": "FAQ 목록을 반환합니다.", "model": BaseListResponse[FaqInfo]},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_faq_list(
    page: int = Query(
        1, ge=1,
        description="조회할 페이지 번호입니다.",
        examples=[1, 2, 3]
    ),
    count: int = Query(
        20, ge=0, le=100,
        description="페이지당 항목 수입니다. (최대 100개, 0이면 전체 조회)",
        examples=[10, 20, 50, 100]
    ),
    category_id: Optional[UUID] = Query(
        None,
        description="카테고리 ID로 필터링합니다."
    ),
    status: Optional[FaqStatus] = Query(
        None,
        description="상태로 필터링합니다.",
        examples=list(FaqStatus)
    ),
    visibility: Optional[FaqVisibility] = Query(
        None,
        description="공개 범위로 필터링합니다.",
        examples=list(FaqVisibility)
    ),
    language: Optional[Language] = Query(
        None,
        description="언어로 필터링합니다.",
        examples=list(Language)
    ),
    department_code: Optional[str] = Query(
        None,
        description="담당 부서 코드로 필터링합니다.",
        examples=["ACAD_AFFAIRS"]
    ),
    keyword: Optional[str] = Query(
        None,
        description="질문 또는 답변 본문에 포함된 검색어입니다.",
        examples=["수강신청", "장학금"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 기본 쿼리 생성
    query = db_models.FaqItem.select()
    count_query = db_models.FaqItem.select(fn.COUNT(db_models.FaqItem.id).alias("count"))

    # 필터 조건 추가
    conditions = []
    if category_id is not None:
        conditions.append(db_models.FaqItem.category_id == category_id)
    if status is not None:
        conditions.append(db_models.FaqItem.status == status.value)
    if visibility is not None:
        conditions.append(db_models.FaqItem.visibility == visibility.value)
    if language is not None:
        conditions.append(db_models.FaqItem.language == language.value)
    if department_code is not None:
        conditions.append(db_models.FaqItem.department_code == department_code)
    if keyword is not None:
        conditions.append((
            (db_models.FaqItem.question.contains(keyword)) |
            (db_models.FaqItem.answer.contains(keyword))
        ))

    # 조건이 있으면 WHERE 절 추가
    if conditions:
        combined_condition = conditions[0]
        for condition in conditions[1:]:
            combined_condition = combined_condition & condition
        query = query.where(combined_condition)
        count_query = count_query.where(combined_condition)

    # 정렬 및 페이징 처리 (count가 0이면 전체 조회)
    query = query.order_by(db_models.FaqItem.created_at.desc())
    if count > 0:
        query = query.offset((page - 1) * count).limit(count)

    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    # 목록 조회
    faqs: list[db_items.FaqItem] = await db_manager.select_items(query)

    # 카테고리 정보 조회
    category_map: dict[UUID, db_items.FaqCategory] = {}
    category_ids = {faq.category_id for faq in faqs}
    if category_ids:
        category_query = (db_models.FaqCategory.select()
                          .where(db_models.FaqCategory.id.in_(list(category_ids))))
        categories: list[db_items.FaqCategory] = await db_manager.select_items(category_query)
        category_map = {category.id: category for category in categories}

    items = [_to_faq_info(faq, category_map.get(faq.category_id)) for faq in faqs]

    # 총 페이지 수 계산
    if count > 0:
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    else:
        total_pages = 1

    return BaseListResponse[FaqInfo](
        total_pages=total_pages,
        total_count=total_count,
        items=items
    )

@router.get("/info/{faq_id}", summary="FAQ 상세 조회",
    description=(
        "FAQ 상세 정보와 색인 상태를 조회합니다.  \n"
        "- 색인된 적이 없으면 `index`는 NULL입니다.  \n"
        "- `index.is_stale`이 **true**이면 원문이 변경되어 재색인이 필요합니다."
    ),
    responses={
        200: {"description": "FAQ 상세 정보를 반환합니다.", "model": FaqDetailResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 FAQ",
                            "value": {
                                "message": "등록되지 않은 FAQ입니다.",
                                "target": "faq_id={faq_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_faq_info(
    faq_id: UUID = Path(
        ...,
        description="조회할 FAQ ID입니다."
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # FAQ 조회
    faq = await _get_faq_by_id(db_manager, faq_id)
    if not faq:
        return NotFoundResponse(
            message="등록되지 않은 FAQ입니다.",
            target=f"faq_id={faq_id}"
        )

    # 카테고리 조회
    category = await _get_category_by_id(db_manager, faq.category_id)

    # 색인 상태 조회
    index = await _get_faq_index(db_manager, faq_id)

    return FaqDetailResponse(
        faq=_to_faq_info(faq, category),
        index=_to_index_info(index, faq) if index else None
    )

@router.post("/create", summary="FAQ 생성",
    description=(
        "FAQ를 생성합니다.  \n"
        "- `status`가 **published**이면 생성 직후 색인을 시도합니다.  \n"
        "- 색인에 실패해도 FAQ 저장은 정상 처리되며, `index_warning`에 사유가 담깁니다."
    ),
    responses={
        200: {"description": "FAQ 생성 결과를 반환합니다.", "model": FaqMutationResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found_category": {
                            "summary": "등록되지 않은 카테고리",
                            "value": {
                                "message": "등록되지 않은 카테고리입니다.",
                                "target": "category_id={category_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def create_faq(
    payload: CreateFaqPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 카테고리 확인
    category = await _get_category_by_id(db_manager, payload.category_id)
    if not category:
        return NotFoundResponse(
            message="등록되지 않은 카테고리입니다.",
            target=f"category_id={payload.category_id}"
        )

    # FAQ 생성
    faq_id = uuid4()
    query = db_models.FaqItem.insert(
        id=faq_id,
        category_id=payload.category_id,
        question=payload.question,
        answer=payload.answer,
        question_aliases_json=payload.question_aliases_json,
        tags_json=payload.tags_json,
        source_url=payload.source_url,
        department_code=payload.department_code,
        visibility=_enum_value(payload.visibility),
        status=_enum_value(payload.status),
        language=_enum_value(payload.language),
        version=1
    )
    await db_manager.execute_query(query)

    logger.info(f"FAQ가 생성되었습니다. (faq_id={faq_id}, status={_enum_value(payload.status)})")

    # 공개 상태이면 색인 시도 (벡터 색인 실패가 생성 자체를 실패시키지 않는다)
    indexed = False
    index_warning = None
    if payload.status == FaqStatus.PUBLISHED:
        faq = await _get_faq_by_id(db_manager, faq_id)
        indexed, index_warning = await _try_sync_faq(
            db_manager=db_manager,
            user_info=user_info,
            faq=faq,
            logger=logger
        )

    return FaqMutationResponse(
        message="FAQ가 생성되었습니다.",
        id=faq_id,
        version=1,
        indexed=indexed,
        index_warning=index_warning
    )

@router.patch("/update/{faq_id}", summary="FAQ 수정",
    description=(
        "FAQ 정보를 수정합니다.  \n"
        "- 요청에 포함된 항목만 수정되며, 실제로 변경된 값이 있으면 `version`이 1 증가합니다.  \n"
        "- `question` 또는 `question_aliases_json`이 변경되면 기존 색인을 재색인 대상으로 표시한 뒤 재색인을 시도합니다.  \n"
        "- `status`가 변경되면 색인 상태를 다시 맞춥니다. (**published**가 아니면 벡터를 제거합니다.)  \n"
        "- 색인에 실패해도 수정은 정상 처리되며, `index_warning`에 사유가 담깁니다."
    ),
    responses={
        200: {"description": "FAQ 수정 결과를 반환합니다.", "model": FaqMutationResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 FAQ",
                            "value": {
                                "message": "등록되지 않은 FAQ입니다.",
                                "target": "faq_id={faq_id}"
                            }
                        },
                        "not_found_category": {
                            "summary": "등록되지 않은 카테고리",
                            "value": {
                                "message": "등록되지 않은 카테고리입니다.",
                                "target": "category_id={category_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_faq(
    payload: UpdateFaqPayload,
    faq_id: UUID = Path(
        ...,
        description="수정할 FAQ ID입니다."
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # FAQ 조회
    faq = await _get_faq_by_id(db_manager, faq_id)
    if not faq:
        return NotFoundResponse(
            message="등록되지 않은 FAQ입니다.",
            target=f"faq_id={faq_id}"
        )

    # 필수 항목은 NULL로 비울 수 없음
    update_values = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if not (value is None and key in FAQ_UPDATE_NON_NULLABLE_FIELDS)
    }

    # 카테고리 확인
    if "category_id" in update_values:
        category = await _get_category_by_id(db_manager, update_values["category_id"])
        if not category:
            return NotFoundResponse(
                message="등록되지 않은 카테고리입니다.",
                target=f"category_id={update_values['category_id']}"
            )

    # 실제로 변경된 값만 반영
    changed_values = {
        key: value
        for key, value in update_values.items()
        if getattr(faq, key, None) != value
    }
    if not changed_values:
        return FaqMutationResponse(
            message="변경된 내용이 없습니다.",
            id=faq_id,
            version=faq.version,
            indexed=False
        )

    # 원문(질문·유사 질문) 변경 여부에 따라 재색인 대상 판정
    is_text_changed = ("question" in changed_values) or ("question_aliases_json" in changed_values)
    is_status_changed = "status" in changed_values

    new_version = faq.version + 1
    db_values = {key: _enum_value(value) for key, value in changed_values.items()}
    db_values["version"] = new_version
    db_values["updated_at"] = util.get_now()

    query = (db_models.FaqItem.update(**db_values)
             .where(db_models.FaqItem.id == faq_id))
    await db_manager.execute_query(query)

    logger.info(f"FAQ가 수정되었습니다. (faq_id={faq_id}, version={new_version})")

    # 원문이 바뀌었으면 기존 색인을 재색인 대상으로 표시
    if is_text_changed:
        try:
            await mark_faq_stale(db_manager, faq_id)
        except Exception:
            logger.exception(f"FAQ 색인 상태 갱신 중 오류가 발생했습니다. (faq_id={faq_id})")

    # 색인 재동기화 시도
    indexed = False
    index_warning = None
    if is_text_changed or is_status_changed:
        faq = await _get_faq_by_id(db_manager, faq_id)
        indexed, index_warning = await _try_sync_faq(
            db_manager=db_manager,
            user_info=user_info,
            faq=faq,
            logger=logger
        )

    return FaqMutationResponse(
        message="FAQ가 수정되었습니다.",
        id=faq_id,
        version=new_version,
        indexed=indexed,
        index_warning=index_warning
    )

@router.delete("/delete/{faq_id}", summary="FAQ 삭제",
    description=(
        "FAQ와 색인된 벡터를 함께 삭제합니다.  \n"
        "- 벡터 삭제에 실패해도 FAQ 삭제는 정상 처리됩니다."
    ),
    responses={
        200: {"description": "FAQ가 삭제되었습니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 FAQ",
                            "value": {
                                "message": "등록되지 않은 FAQ입니다.",
                                "target": "faq_id={faq_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_faq(
    faq_id: UUID = Path(
        ...,
        description="삭제할 FAQ ID입니다."
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # FAQ 조회
    faq = await _get_faq_by_id(db_manager, faq_id)
    if not faq:
        return NotFoundResponse(
            message="등록되지 않은 FAQ입니다.",
            target=f"faq_id={faq_id}"
        )

    # 벡터·색인 레코드 삭제 (벡터 삭제 실패가 삭제 자체를 실패시키지 않는다)
    try:
        await delete_faq_vectors(db_manager, faq_id)
    except Exception:
        logger.exception(f"FAQ 벡터 삭제 중 오류가 발생했습니다. (faq_id={faq_id})")

    # FAQ 삭제
    query = (db_models.FaqItem.delete()
             .where(db_models.FaqItem.id == faq_id))
    await db_manager.execute_query(query)

    logger.info(f"FAQ가 삭제되었습니다. (faq_id={faq_id})")

    return BaseMessageResponse(message="FAQ가 삭제되었습니다.")

@router.post("/sync", summary="FAQ 색인 동기화",
    description=(
        "FAQ를 벡터 지식베이스에 동기화합니다.  \n"
        "- `faq_ids`를 지정하지 않으면 공개(**published**) 상태의 전체 FAQ가 대상입니다.  \n"
        "- 원문·임베딩 모델·임베딩 버전이 모두 동일하면 건너뜁니다. (`force`로 강제 재색인)  \n"
        "- 공개 상태가 아닌 FAQ는 색인 대상에서 제외되고 기존 벡터가 삭제됩니다.  \n"
        "- 건별 실패는 응답의 `results`에 담기며 전체 요청은 실패로 처리되지 않습니다."
    ),
    responses={
        200: {"description": "색인 동기화 요약을 반환합니다.", "model": FaqSyncResponse},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def sync_faq(
    payload: SyncFaqPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 동기화 대상 조회
    query = db_models.FaqItem.select()
    if payload.faq_ids:
        query = query.where(db_models.FaqItem.id.in_(payload.faq_ids))
    else:
        query = query.where(db_models.FaqItem.status == FaqStatus.PUBLISHED.value)
    query = query.order_by(db_models.FaqItem.created_at.asc())
    faqs: list[db_items.FaqItem] = await db_manager.select_items(query)

    if not faqs:
        return FaqSyncResponse(
            total_count=0,
            success_count=0,
            skipped_count=0,
            failed_count=0,
            results=[]
        )

    # 컬렉션·임베딩 모델 준비 (준비 실패 시 경고만 반환한다)
    try:
        collection, error_message = await ensure_faq_collection(
            db_manager=db_manager,
            embedding_model_name=config.chatbot.embedding_model,
            user_info=user_info
        )
    except Exception:
        logger.exception("FAQ 지식베이스 컬렉션 준비 중 오류가 발생했습니다.")
        collection, error_message = None, "FAQ 지식베이스 컬렉션을 준비하지 못했습니다."

    if not collection:
        logger.warning(f"FAQ 색인 동기화를 진행할 수 없습니다. (reason={error_message})")
        return FaqSyncResponse(
            total_count=len(faqs),
            success_count=0,
            skipped_count=0,
            failed_count=len(faqs),
            results=[],
            warning=error_message or "FAQ 지식베이스 컬렉션을 준비하지 못했습니다."
        )

    model = await get_embedding_model(db_manager, collection)
    if not model:
        logger.warning(f"FAQ 임베딩 모델을 사용할 수 없습니다. (model={collection.embedding_model})")
        return FaqSyncResponse(
            total_count=len(faqs),
            success_count=0,
            skipped_count=0,
            failed_count=len(faqs),
            results=[],
            warning="FAQ 지식베이스의 임베딩 모델을 사용할 수 없습니다."
        )

    # 건별 색인
    results: list[FaqSyncResult] = []
    for faq in faqs:
        try:
            results.append(await sync_faq_item(
                db_manager=db_manager,
                collection=collection,
                model=model,
                user_info=user_info,
                faq=faq,
                force=payload.force
            ))
        except Exception as exc:
            logger.exception(f"FAQ 색인 중 오류가 발생했습니다. (faq_id={faq.id})")
            results.append(FaqSyncResult(
                faq_id=faq.id,
                vector_status=VectorStatus.FAILED,
                error_message=str(exc)[:1024]
            ))

    skipped_count = len([result for result in results if result.skipped])
    failed_count = len([result for result in results if result.vector_status == VectorStatus.FAILED])
    success_count = len(results) - skipped_count - failed_count

    logger.info(
        f"FAQ 색인 동기화가 완료되었습니다. "
        f"(total={len(results)}, success={success_count}, skipped={skipped_count}, failed={failed_count})"
    )

    return FaqSyncResponse(
        total_count=len(results),
        success_count=success_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        results=results
    )

@router.post("/search", summary="FAQ 유사도 검색",
    description=(
        "FAQ 지식베이스에서 유사한 질문을 검색합니다.  \n"
        "- 공개(**published**) 상태의 FAQ만 검색됩니다.  \n"
        "- `top_k`와 `score_threshold`를 지정하지 않으면 챗봇 설정값을 사용합니다.  \n"
        "- 검색 소요 시간(`latency_ms`)을 함께 반환합니다."
    ),
    responses={
        200: {"description": "검색 결과를 반환합니다.", "model": FaqSearchResponse},
        400: {
            "description": "요청이 올바르지 않습니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_ready": {
                            "summary": "지식베이스 미준비",
                            "value": {
                                "message": "FAQ 지식베이스 컬렉션이 준비되지 않았습니다.",
                                "target": None
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def search_faq_items(
    payload: SearchFaqPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    top_k = payload.top_k or config.chatbot.top_k
    score_threshold = payload.score_threshold if payload.score_threshold is not None else config.chatbot.score_threshold

    try:
        results, latency_ms = await search_faq(
            db_manager=db_manager,
            user_info=user_info,
            query_text=payload.query,
            top_k=top_k,
            score_threshold=score_threshold,
            language=payload.language,
            category_code=payload.category_code
        )
    except ValueError as exc:
        # 컬렉션·임베딩 모델이 준비되지 않은 경우
        logger.warning(f"FAQ 검색을 수행할 수 없습니다. ({exc})")
        return BadRequestResponse(message=str(exc))
    except Exception:
        logger.exception("FAQ 검색 중 오류가 발생했습니다.")
        return InternalServerErrorResponse(message="FAQ 검색 중 오류가 발생했습니다.")

    return FaqSearchResponse(
        query=payload.query,
        results=results,
        total_count=len(results),
        score_threshold=score_threshold,
        latency_ms=latency_ms
    )

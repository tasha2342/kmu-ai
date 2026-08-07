
from logging import Logger

from typing import Optional

from peewee import fn

from fastapi import APIRouter, Depends, Path, Query

from botocore.exceptions import ClientError

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
)
from app.payloads.v1.collection import (
    CreateCollectionPayload,
    UpdateCollectionPayload,
)
from app.responses.base import BaseMessageResponse
from app.responses.v1.collection import (
    CollectionInfo,
    CollectionsListResponse,
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
from app.models.enum import ModelType, ModelStatus, GraphIndexStatus, DocumentStatus
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.s3 import get_s3_manager, S3Manager
from app.utils.vector_store import (
    get_vector_store_manager,
    VectorStoreManager,
    SUPPORTED_VECTOR_SIZES,
)
from app.utils.graphrag import get_graphrag_service, GraphRAGService
from app.utils.litellm import create_embedding
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.access_scope as access_scope
import app.utils.common as util


router = APIRouter()


@router.get("/list", summary="컬렉션 목록 조회",
    description="컬렉션 목록과 통계를 조회합니다.",
    responses={
        200: {"description": "컬렉션 목록을 반환합니다.", "model": CollectionsListResponse},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_collections(
    is_system: bool = Query(
        False,
        description="시스템 전용 컬렉션도 포함하여 조회합니다.",
        examples=[False, True]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # Collection 테이블에서 목록 조회
    query = db_models.Collection.select()
    if not is_system:
        query = query.where(db_models.Collection.is_system == False)
    
    scope_expr = access_scope.peewee_access_scope_filter(db_models.Collection.access_scope, user_info)
    if scope_expr is not None:
        query = query.where(scope_expr)
    collections_list: list[db_items.Collection] = await db_manager.select_items(query)
    
    # 컬렉션별 문서/청크 통계 조회
    stats_query = (db_models.Document.select(
        db_models.Document.collection_name,
        fn.COUNT(db_models.Document.id).alias("document_count"),
        fn.COALESCE(fn.SUM(db_models.Document.chunk_count), 0).alias("chunk_count")
    ).where(db_models.Document.status == DocumentStatus.COMPLETED.value)
     .group_by(db_models.Document.collection_name))
    stats_results = await db_manager.execute_query(stats_query)
    
    # 통계를 dict로 변환
    stats_map = {
        row.collection_name: {
            "document_count": row.document_count,
            "chunk_count": row.chunk_count
        }
        for row in stats_results
    }
    
    collections = [
        CollectionInfo(
            name=col.name,
            user_name=col.user_name,
            embedding_model=col.embedding_model,
            vector_size=col.vector_size,
            description=col.description,
            access_scope=col.access_scope,
            is_system=col.is_system,
            is_active=getattr(col, "is_active", True),
            document_count=stats_map.get(col.name, {}).get("document_count", 0),
            chunk_count=stats_map.get(col.name, {}).get("chunk_count", 0),
            graph_index_status=col.graph_index_status,
            graph_index_error=col.graph_index_error,
            graph_indexed_at=util.serialize_datetime(col.graph_indexed_at),
            created_at=util.serialize_datetime(col.created_at),
            updated_at=util.serialize_datetime(col.updated_at)
        )
        for col in collections_list
    ]
    
    return CollectionsListResponse(
        collections=collections,
        total_count=len(collections)
    )

@router.post("/create", summary="컬렉션 생성",
    description=(
        "새로운 컬렉션을 생성합니다.  \n"
        "- 임베딩 모델을 지정하여 컬렉션을 생성합니다.  \n"
        "- 생성 후에는 임베딩 모델과 벡터 크기를 변경할 수 없습니다.  \n"
        "- 임베딩 모델의 벡터 차원이 지원 목록에 없으면 생성할 수 없습니다."
    ),
    responses={
        200: {"description": "컬렉션 생성 결과를 반환합니다.", "model": CollectionInfo},
        400: {
            "description": "요청이 올바르지 않습니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "unsupported_vector_size": {
                            "summary": "지원하지 않는 임베딩 차원",
                            "value": {
                                "message": "지원하지 않는 임베딩 차원입니다. (vector_size={vector_size}) 지원 차원: 384, 768, 1024, 1536, 3072",
                                "target": "embedding_model={embedding_model}"
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
                        "not_found_model": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "embedding_model={embedding_model}"
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
                            "summary": "이미 존재하는 컬렉션",
                            "value": {
                                "message": "이미 존재하는 컬렉션입니다.",
                                "target": "name={name}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def create_collection(
    payload: CreateCollectionPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 임베딩 모델 확인
    query = (db_models.Model.select()
             .where(db_models.Model.name == payload.embedding_model)
             .where(db_models.Model.model_type == ModelType.EMBEDDING.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"embedding_model={payload.embedding_model}"
        )
    
    # 컬렉션 존재 확인
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == payload.name))
    existing_collection = await db_manager.select_item(query)
    if existing_collection:
        return ConflictResponse(
            message="이미 존재하는 컬렉션입니다.",
            target=f"name={payload.name}"
        )
    
    # 임베딩 테스트로 벡터 크기 확인
    try:
        test_response = await create_embedding(model, user_info, ["test"])
        vector_size = len(test_response.data[0]["embedding"])
    except Exception:
        logger.exception("임베딩 모델 테스트 중 오류가 발생했습니다.")
        return InternalServerErrorResponse(
            message="임베딩 모델 테스트 중 오류가 발생했습니다."
        )
    
    try:
        scope_value = access_scope.validate_access_scope(payload.access_scope)
    except ValueError as exc:
        return BadRequestResponse(message=str(exc), target=f"access_scope={payload.access_scope}")
    
    # 벡터 저장소는 차원별 고정 테이블을 쓰므로 지원 목록 밖의 차원은 저장할 수 없다
    if vector_size not in SUPPORTED_VECTOR_SIZES:
        return BadRequestResponse(
            message=(
                f"지원하지 않는 임베딩 차원입니다. (vector_size={vector_size}) "
                f"지원 차원: {', '.join(str(size) for size in SUPPORTED_VECTOR_SIZES)}"
            ),
            target=f"embedding_model={payload.embedding_model}"
        )
    
    # 벡터 저장소 컬렉션 준비 (거리 측정은 코사인 고정)
    await vector_store.create_collection_async(
        collection_name=payload.name,
        vector_size=vector_size
    )
    
    # DB 컬렉션 생성
    query = db_models.Collection.insert(
        name=payload.name,
        user_name=user_info.username,
        embedding_model=payload.embedding_model,
        vector_size=vector_size,
        description=payload.description,
        access_scope=scope_value,
        is_system=payload.is_system,
        graph_index_status=GraphIndexStatus.NO_INDEXED
    )
    await db_manager.execute_query(query)
    
    logger.info(f"컬렉션이 생성되었습니다. (collection_name={payload.name})")
    
    return CollectionInfo(
        name=payload.name,
        user_name=user_info.username,
        embedding_model=payload.embedding_model,
        vector_size=vector_size,
        description=payload.description,
        access_scope=scope_value,
        is_system=payload.is_system,
        is_active=True,
        document_count=0,
        chunk_count=0,
        graph_index_status=GraphIndexStatus.NO_INDEXED,
        graph_index_error=None,
        graph_indexed_at=None,
        created_at=util.serialize_datetime(util.get_now()),
        updated_at=util.serialize_datetime(util.get_now())
    )

@router.patch("/update/{collection_name}", summary="컬렉션 수정",
    description=(
        "컬렉션 정보를 수정합니다.  \n"
        "- 임베딩 모델과 벡터 크기는 변경할 수 없습니다.  \n"
        "- 설명, 접근 범위, 시스템 전용 여부를 수정할 수 있습니다."
    ),
    responses={
        200: {"description": "컬렉션 수정 결과를 반환합니다.", "model": CollectionInfo},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 컬렉션",
                            "value": {
                                "message": "컬렉션을 찾을 수 없습니다.",
                                "target": "collection_name={collection_name}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_collection(
    payload: UpdateCollectionPayload,
    collection_name: str = Path(
        ...,
        description="수정할 컬렉션 이름입니다.",
        examples=["test"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 컬렉션 조회
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={collection_name}"
        )
    
    # 업데이트
    update_data = {}
    payload_set = payload.model_dump(exclude_unset=True)
    if "description" in payload_set and payload_set["description"] is not None:
        update_data["description"] = payload_set["description"]
    if "access_scope" in payload_set:
        try:
            update_data["access_scope"] = access_scope.validate_access_scope(payload_set["access_scope"])
        except ValueError as exc:
            return BadRequestResponse(message=str(exc), target=f"access_scope={payload_set['access_scope']}")
    if "is_system" in payload_set and payload_set["is_system"] is not None:
        update_data["is_system"] = payload_set["is_system"]
    if "is_active" in payload_set and payload_set["is_active"] is not None:
        update_data["is_active"] = payload_set["is_active"]
    
    if update_data:
        update_data["updated_at"] = util.get_now()
        query = (db_models.Collection.update(**update_data)
                 .where(db_models.Collection.name == collection_name))
        await db_manager.execute_query(query)
        query = (db_models.Collection.select()
                 .where(db_models.Collection.name == collection_name))
        collection = await db_manager.select_item(query)
    
    # 문서/청크 통계 조회
    stats_query = (db_models.Document.select(
        fn.COUNT(db_models.Document.id).alias("document_count"),
        fn.COALESCE(fn.SUM(db_models.Document.chunk_count), 0).alias("chunk_count")
    ).where(db_models.Document.collection_name == collection_name)
     .where(db_models.Document.status == DocumentStatus.COMPLETED.value))
    stats_result = await db_manager.execute_query(stats_query)
    doc_count = stats_result[0].document_count if stats_result else 0
    chunk_count = stats_result[0].chunk_count if stats_result else 0
    
    logger.debug(f"컬렉션이 수정되었습니다. (collection_name={collection_name})")
    
    return CollectionInfo(
        name=collection.name,
        user_name=collection.user_name,
        embedding_model=collection.embedding_model,
        vector_size=collection.vector_size,
        description=collection.description,
        access_scope=collection.access_scope,
        is_system=collection.is_system,
        is_active=getattr(collection, "is_active", True),
        document_count=doc_count,
        chunk_count=chunk_count,
        graph_index_status=collection.graph_index_status,
        graph_index_error=collection.graph_index_error,
        graph_indexed_at=util.serialize_datetime(collection.graph_indexed_at),
        created_at=util.serialize_datetime(collection.created_at),
        updated_at=util.serialize_datetime(collection.updated_at)
    )

@router.delete("/delete/{collection_name}", summary="컬렉션 삭제",
    description=(
        "컬렉션과 관련된 모든 데이터를 삭제합니다.  \n"
        "- 문서 파일 삭제  \n"
        "- Vector 데이터 삭제  \n"
        "- 그래프 데이터 삭제"
    ),
    responses={
        200: {"description": "컬렉션 삭제 결과를 반환합니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 컬렉션",
                            "value": {
                                "message": "컬렉션을 찾을 수 없습니다.",
                                "target": "collection_name={collection_name}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_collection(
    collection_name: str = Path(
        ...,
        description="삭제할 컬렉션 이름입니다.",
        examples=["test"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    s3_manager: S3Manager = Depends(get_s3_manager),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 컬렉션 조회
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={collection_name}"
        )
    
    # 컬렉션의 문서 조회
    query = (db_models.Document.select()
             .where(db_models.Document.collection_name == collection_name))
    documents: list[db_items.Document] = await db_manager.select_items(query)
    
    # 문서 파일 삭제
    for doc in documents:
        try:
            s3_manager.delete_file(doc.file_path)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code != "404":
                logger.exception(f"파일 삭제 중 오류가 발생했습니다. (file_path={doc.file_path})")
    
    # 문서 DB 삭제
    query = (db_models.Document.delete()
             .where(db_models.Document.collection_name == collection_name))
    await db_manager.execute_query(query)
    
    # 벡터 청크 삭제
    if await vector_store.collection_exists_async(collection_name):
        await vector_store.delete_collection_async(collection_name)
    
    # GraphRAG 그래프 데이터 삭제
    graphrag_service.delete_collection_graph(collection_name)
    
    # Collection DB 삭제
    query = (db_models.Collection.delete()
             .where(db_models.Collection.name == collection_name))
    await db_manager.execute_query(query)
    
    logger.info(f"컬렉션이 삭제되었습니다. (collection_name={collection_name}, docs={len(documents)})")
    
    return BaseMessageResponse(
        message="컬렉션이 성공적으로 삭제되었습니다."
    )

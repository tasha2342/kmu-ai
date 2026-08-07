
from logging import Logger

from typing import Optional

from fastapi import APIRouter, Depends, BackgroundTasks, Path, Query
from fastapi.responses import ORJSONResponse

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
)
from app.payloads.v1.graphrag import (
    GraphIndexPayload,
    GraphSearchPayload,
    HybridSearchPayload,
    FacilitySyncPayload,
    GraphKeywordSearchPayload,
)
from app.responses.base import BaseMessageResponse
from app.responses.v1.graphrag import (
    GraphStatsResponse,
    GraphIndexResponse,
    FacilitySyncResponse,
    EntityInfo,
    RelationshipInfo,
    CommunityInfo,
    DocumentChunk,
    GraphSearchResponse,
    VectorSearchResult,
    HybridSearchResponse,
    GraphNode,
    GraphEdge,
    GraphVisualizationResponse,
)
from app.responses.exception import (
    BadRequestResponse,
    ForbiddenResponse,
    NotFoundResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import (
    BadRequestError,
    NotFoundError,
)
from app.models.enum import ModelType, ModelStatus, GraphIndexStatus, DocumentStatus
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.vector_store import get_vector_store_manager, VectorStoreManager
from app.utils.graphrag import get_graphrag_service, GraphRAGService
from app.utils.litellm import create_embedding
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.access_scope as access_scope
import app.utils.common as util


router = APIRouter()


@router.get("/stats/{collection_name}", summary="그래프 통계 조회",
    description="컬렉션의 그래프 통계를 조회합니다.",
    responses={
        200: {"description": "그래프 통계를 반환합니다.", "model": GraphStatsResponse},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_graph_stats(
    collection_name: str = Path(
        ...,
        description="통계를 조회할 컬렉션 이름입니다.",
        examples=["test"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={collection_name}"
        )
    
    if not access_scope.can_access_scope(user_info, collection.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    stats = graphrag_service.get_collection_stats(collection_name)
    
    return GraphStatsResponse(
        collection_name=collection_name,
        entity_count=stats["entity_count"],
        relationship_count=stats["relationship_count"],
        community_count=stats["community_count"]
    )

@router.post("/index", summary="문서 인덱싱",
    description=(
        "컬렉션의 문서들을 분석하여 지식 그래프를 생성합니다.  \n"
        "- 문서에서 엔티티(개체)와 관계를 추출합니다.  \n"
        "- 커뮤니티를 탐지하고 요약을 생성합니다.  \n"
        "- 이미 인덱싱된 컬렉션은 기존 그래프를 삭제하고 새로 생성합니다."
    ),
    responses={
        200: {"description": "인덱싱 결과를 반환합니다.", "model": GraphIndexResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "already_indexing": {
                            "summary": "이미 인덱싱 중",
                            "value": {
                                "message": "이미 인덱싱이 진행 중입니다.",
                                "target": "collection_name={collection_name}"
                            }
                        },
                        "no_documents": {
                            "summary": "처리할 문서가 없음",
                            "value": {
                                "message": "처리할 문서가 없습니다.",
                                "target": "collection_name={collection_name}"
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
                        "not_found_text_model": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "text_model={text_model}"
                            }
                        },
                        "not_found_collection": {
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
async def index_collection(
    payload: GraphIndexPayload,
    background_tasks: BackgroundTasks,
    db_manager: DatabaseManager = Depends(get_db_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 컬렉션 존재 확인
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == payload.collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={payload.collection_name}"
        )
    
    # 이미 인덱싱 중인지 확인
    if collection.graph_index_status == GraphIndexStatus.PROCESSING:
        return BadRequestResponse(
            message="이미 인덱싱이 진행 중입니다.",
            target=f"collection_name={payload.collection_name}"
        )
    
    # 텍스트 생성 모델 확인
    query = (db_models.Model.select()
             .where(db_models.Model.name == payload.text_model)
             .where(db_models.Model.model_type == ModelType.TEXT_GENERATION.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"text_model={payload.text_model}"
        )
    
    # 컬렉션의 문서 조회
    query = (db_models.Document.select()
             .where(db_models.Document.collection_name == payload.collection_name)
             .where(db_models.Document.status == DocumentStatus.COMPLETED.value))
    documents: list[db_items.Document] = await db_manager.select_items(query)
    
    if not documents:
        return BadRequestResponse(
            message="처리할 문서가 없습니다.",
            target=f"collection_name={payload.collection_name}"
        )
    
    async def _process_indexing() -> ORJSONResponse:
        try:
            # 기존 그래프 삭제
            graphrag_service.delete_collection_graph(payload.collection_name)
            
            logger.info(f"{len(documents)}개의 문서 인덱싱 시작 (collection={payload.collection_name})")
            
            total_entities = 0
            total_relationships = 0
            
            # 각 문서의 청크에서 엔티티/관계 추출
            for doc in documents:
                # 벡터 저장소에서 청크 조회
                try:
                    chunks = await vector_store.get_points_by_document_id_async(
                        payload.collection_name,
                        doc.id
                    )
                except Exception:
                    logger.exception(f"문서 청크 조회 중 오류가 발생했습니다. (document_id={doc.id})")
                    continue
                
                for chunk in chunks:
                    content = chunk.get("content", "")
                    chunk_index = chunk.get("chunk_index", 0)
                    
                    if not content:
                        continue
                    
                    # 엔티티/관계 추출
                    extraction = await graphrag_service.extract_entities_and_relationships(
                        text=content,
                        model=model,
                        user_info=user_info
                    )
                    
                    # 그래프에 저장
                    entity_count = graphrag_service.store_entities(
                        payload.collection_name,
                        doc.id,
                        chunk_index,
                        extraction.entities
                    )
                    
                    relationship_count = graphrag_service.store_relationships(
                        payload.collection_name,
                        doc.id,
                        extraction.relationships
                    )
                    
                    total_entities += entity_count
                    total_relationships += relationship_count
            
            # 커뮤니티 탐지
            community_count = graphrag_service.detect_communities(payload.collection_name)
            
            # 커뮤니티 요약 생성
            if payload.generate_summaries and community_count > 0:
                await graphrag_service.generate_community_summaries(
                    collection_name=payload.collection_name,
                    model=model,
                    user_info=user_info
                )
            
            # DB 업데이트 (상태: completed)
            query = (db_models.Collection.update(
                graph_index_status=GraphIndexStatus.INDEXED,
                graph_index_error=None,
                graph_indexed_at=util.get_now(),
                updated_at=util.get_now()
            ).where(db_models.Collection.name == payload.collection_name))
            await db_manager.execute_query(query)
            
            logger.info(f"문서 인덱싱 완료 (collection={payload.collection_name}): "
                        f"Entity={total_entities}, Relationship={total_relationships}, "
                        f"Community={community_count}")
            
            return GraphIndexResponse(
                collection_name=payload.collection_name,
                status=GraphIndexStatus.INDEXED,
                document_count=len(documents),
                entity_count=total_entities,
                relationship_count=total_relationships,
                community_count=community_count,
                message="문서 인덱싱이 완료되었습니다."
            )
        except Exception as exc:
            logger.exception("문서 인덱싱 중 오류가 발생했습니다.")
            
            # DB 업데이트 (상태: error)
            error_message = str(exc)[:1000]
            query = (db_models.Collection.update(
                graph_index_status=GraphIndexStatus.ERROR,
                graph_index_error=error_message,
                updated_at=util.get_now()
            ).where(db_models.Collection.name == payload.collection_name))
            await db_manager.execute_query(query)
            
            return GraphIndexResponse(
                collection_name=payload.collection_name,
                status=GraphIndexStatus.ERROR,
                message=f"인덱싱 중 오류가 발생했습니다: {error_message}"
            )
    
    # 백그라운드에서 처리
    if payload.background:
        # DB 업데이트 (상태: processing)
        query = (db_models.Collection.update(
            graph_index_status=GraphIndexStatus.PROCESSING,
            graph_index_error=None,
            updated_at=util.get_now()
        ).where(db_models.Collection.name == payload.collection_name))
        await db_manager.execute_query(query)
        
        background_tasks.add_task(_process_indexing)
        return GraphIndexResponse(
            collection_name=payload.collection_name,
            status=GraphIndexStatus.PROCESSING,
            document_count=len(documents),
            message="인덱싱이 백그라운드에서 시작되었습니다. 컬렉션 목록에서 상태를 확인하세요."
        )
    
    # DB 업데이트 (상태: processing)
    query = (db_models.Collection.update(
        graph_index_status=GraphIndexStatus.PROCESSING,
        graph_index_error=None,
        updated_at=util.get_now()
    ).where(db_models.Collection.name == payload.collection_name))
    await db_manager.execute_query(query)
    
    return await _process_indexing()

@router.post("/facilities/sync", summary="시설(Facility) 데이터 동기화 (구조화 적재)",
    description=(
        "pet-pass-one의 Facility 데이터를 LLM 엔티티 추출 없이 그래프에 직접 upsert합니다.  \n"
        "이름/유형/주소/연락처가 이미 정형화되어 있으므로 문서 인덱싱(`/index`)과 달리 LLM 호출이 없습니다.  \n"
        "대상 컬렉션이 없으면 벡터 없는 시스템 컬렉션으로 자동 생성합니다."
    ),
    responses={
        200: {"description": "동기화 결과를 반환합니다.", "model": FacilitySyncResponse},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def sync_facilities(
    payload: FacilitySyncPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 대상 컬렉션 존재 확인, 없으면 시스템 컬렉션으로 자동 생성
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == payload.collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)

    if not collection:
        insert_query = db_models.Collection.insert(
            name=payload.collection_name,
            user_name=user_info.username,
            embedding_model="none",
            vector_size=0,
            description="구조화 데이터 동기화 전용 시스템 컬렉션입니다. (벡터/문서 없음)",
            access_scope=None,
            is_system=True,
            graph_index_status=GraphIndexStatus.NO_INDEXED,
        )
        await db_manager.execute_query(insert_query)
        logger.info(f"시설 동기화용 시스템 컬렉션을 생성했습니다. (collection_name={payload.collection_name})")

    synced_count = graphrag_service.store_facility_entities(
        payload.collection_name,
        [item.model_dump() for item in payload.facilities]
    )

    logger.info(f"시설 {synced_count}건이 동기화되었습니다. (collection_name={payload.collection_name})")

    return FacilitySyncResponse(
        collection_name=payload.collection_name,
        synced_count=synced_count,
        message=f"{synced_count}개의 시설이 동기화되었습니다."
    )

@router.post("/search", summary="문서 검색 (그래프 기반)",
    description="지식 그래프를 활용하여 검색합니다.",
    responses={
        200: {"description": "검색 결과를 반환합니다.", "model": GraphSearchResponse},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found_text_model": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "text_model={text_model}"
                            }
                        },
                        "not_found_collection": {
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
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def search_graph(
    payload: GraphSearchPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 컬렉션 존재 확인
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == payload.collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={payload.collection_name}"
        )
    
    if not access_scope.can_access_scope(user_info, collection.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    # 텍스트 생성 모델 확인
    query = (db_models.Model.select()
             .where(db_models.Model.name == payload.text_model)
             .where(db_models.Model.model_type == ModelType.TEXT_GENERATION.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"text_model={payload.text_model}"
        )
    
    # 검색 수행
    result = await graphrag_service.search(
        collection_name=payload.collection_name,
        query=payload.query,
        model=model,
        user_info=user_info,
        top_k=payload.top_k
    )
    
    return GraphSearchResponse(
        query=payload.query,
        collection_name=payload.collection_name,
        entities=[
            EntityInfo(
                id=e.get("id", ""),
                name=e.get("name", ""),
                type=e.get("type", ""),
                description=e.get("description"),
                mention_count=e.get("mention_count"),
                community_id=e.get("community_id")
            )
            for e in result.entities
        ],
        relationships=[
            RelationshipInfo(
                source_id=r.get("source_id", ""),
                source_name=r.get("source_name", ""),
                source_type=r.get("source_type", ""),
                target_id=r.get("target_id", ""),
                target_name=r.get("target_name", ""),
                target_type=r.get("target_type", ""),
                relation_type=r.get("relation_type", ""),
                description=r.get("description")
            )
            for r in result.relationships
        ],
        communities=[
            CommunityInfo(
                id=c.get("id", 0),
                summary=c.get("summary"),
                member_count=c.get("member_count")
            )
            for c in result.communities
        ],
        documents=[
            DocumentChunk(
                document_id=d.get("document_id", 0),
                file_name=d.get("file_name", ""),
                chunk_index=d.get("chunk_index", 0),
                content=d.get("content", ""),
                page=d.get("page")
            )
            for d in result.documents
        ]
    )

@router.post("/search_by_keywords", summary="키워드 기반 그래프 검색 (LLM 미사용)",
    description=(
        "구조화 데이터(예: 시설)처럼 쿼리에서 LLM으로 엔티티를 다시 추출할 필요가 없는 경우를 위한 검색입니다.  \n"
        "전달받은 키워드로 바로 매칭하므로 `/search` 대비 저지연 · 무비용입니다."
    ),
    responses={
        200: {"description": "검색 결과를 반환합니다.", "model": GraphSearchResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found_collection": {
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
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def search_graph_by_keywords(
    payload: GraphKeywordSearchPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 컬렉션 존재 확인
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == payload.collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={payload.collection_name}"
        )

    if not access_scope.can_access_scope(user_info, collection.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")

    result = graphrag_service.search_by_keywords(
        collection_name=payload.collection_name,
        keywords=payload.keywords,
        top_k=payload.top_k
    )

    return GraphSearchResponse(
        query=", ".join(payload.keywords),
        collection_name=payload.collection_name,
        entities=[
            EntityInfo(
                id=e.get("id", ""),
                name=e.get("name", ""),
                type=e.get("type", ""),
                description=e.get("description"),
                mention_count=e.get("mention_count"),
                community_id=e.get("community_id")
            )
            for e in result.entities
        ],
        relationships=[
            RelationshipInfo(
                source_id=r.get("source_id", ""),
                source_name=r.get("source_name", ""),
                source_type=r.get("source_type", ""),
                target_id=r.get("target_id", ""),
                target_name=r.get("target_name", ""),
                target_type=r.get("target_type", ""),
                relation_type=r.get("relation_type", ""),
                description=r.get("description")
            )
            for r in result.relationships
        ],
        communities=[],
        documents=[]
    )

@router.post("/hybrid_search", summary="하이브리드 검색 (Vector + Graph)",
    description=(
        "벡터 유사도 검색과 그래프 검색을 결합합니다.  \n"
        "- 벡터 검색: 쿼리와 유사한 청크를 검색  \n"
        "- 그래프 검색: 관련 엔티티와 관계 정보 추가"
    ),
    responses={
        200: {"description": "검색 결과를 반환합니다.", "model": HybridSearchResponse},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found_collection": {
                            "summary": "등록되지 않은 컬렉션",
                            "value": {
                                "message": "컬렉션을 찾을 수 없습니다.",
                                "target": "collection_name={collection_name}"
                            }
                        },
                        "not_found_embedding_model": {
                            "summary": "등록되지 않은 임베딩 모델",
                            "value": {
                                "message": "컬렉션의 임베딩 모델을 찾을 수 없습니다.",
                                "target": "embedding_model={embedding_model}"
                            }
                        },
                        "not_found_text_model": {
                            "summary": "등록되지 않은 텍스트 생성 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "text_model={text_model}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def hybrid_search(
    payload: HybridSearchPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 컬렉션 조회
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == payload.collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={payload.collection_name}"
        )
    
    if not access_scope.can_access_scope(user_info, collection.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    # 컬렉션의 임베딩 모델 확인
    query = (db_models.Model.select()
             .where(db_models.Model.name == collection.embedding_model)
             .where(db_models.Model.model_type == ModelType.EMBEDDING.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    embedding_model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not embedding_model:
        return NotFoundResponse(
            message="컬렉션의 임베딩 모델을 찾을 수 없습니다.",
            target=f"embedding_model={collection.embedding_model}"
        )
    
    # 텍스트 생성 모델 확인
    query = (db_models.Model.select()
             .where(db_models.Model.name == payload.text_model)
             .where(db_models.Model.model_type == ModelType.TEXT_GENERATION.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    text_model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not text_model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"text_model={payload.text_model}"
        )
    
    # 벡터 검색
    embedding_response = await create_embedding(embedding_model, user_info, [payload.query])
    query_vector = embedding_response.data[0]["embedding"]
    
    vector_results = await vector_store.search_async(
        collection_name=payload.collection_name,
        query_vector=query_vector,
        limit=payload.vector_top_k,
        score_threshold=payload.score_threshold
    )
    
    # 그래프 검색
    graph_result = await graphrag_service.search(
        collection_name=payload.collection_name,
        query=payload.query,
        model=text_model,
        user_info=user_info,
        top_k=payload.graph_top_k
    )
    
    # 결과 결합
    vector_search_results = [
        VectorSearchResult(
            document_id=r["payload"].get("document_id", 0),
            file_name=r["payload"].get("file_name", ""),
            chunk_index=r["payload"].get("chunk_index", 0),
            content=r["payload"].get("content", ""),
            page=r["payload"].get("page"),
            score=r["score"]
        )
        for r in vector_results
    ]
    
    return HybridSearchResponse(
        query=payload.query,
        collection_name=payload.collection_name,
        vector_results=vector_search_results,
        entities=[
            EntityInfo(
                id=e.get("id", ""),
                name=e.get("name", ""),
                type=e.get("type", ""),
                description=e.get("description"),
                mention_count=e.get("mention_count"),
                community_id=e.get("community_id")
            )
            for e in graph_result.entities
        ],
        relationships=[
            RelationshipInfo(
                source_id=r.get("source_id", ""),
                source_name=r.get("source_name", ""),
                source_type=r.get("source_type", ""),
                target_id=r.get("target_id", ""),
                target_name=r.get("target_name", ""),
                target_type=r.get("target_type", ""),
                relation_type=r.get("relation_type", ""),
                description=r.get("description")
            )
            for r in graph_result.relationships
        ]
    )

@router.delete("/delete/{collection_name}", summary="그래프 데이터 삭제",
    description="컬렉션의 그래프 데이터를 삭제합니다.",
    responses={
        200: {"description": "삭제 결과를 반환합니다.", "model": BaseMessageResponse},
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
async def delete_graph(
    collection_name: str,
    db_manager: DatabaseManager = Depends(get_db_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 컬렉션 존재 확인
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={collection_name}"
        )
    
    graphrag_service.delete_collection_graph(collection_name)
    
    return BaseMessageResponse(
        message=f"그래프 데이터가 삭제되었습니다."
    )

@router.get("/visualize/{collection_name}", summary="그래프 시각화 데이터 조회",
    description=(
        "UI에서 그래프 시각화를 위한 노드와 엣지 데이터를 반환합니다.  \n"
        "- 노드: 엔티티 (크기는 언급 횟수 기반)  \n"
        "- 엣지: 엔티티 간의 관계 (두께는 가중치 기반)  \n"
        "- D3.js, Cytoscape.js, vis.js 등과 호환되는 형식"
    ),
    responses={
        200: {"description": "그래프 시각화 데이터를 반환합니다.", "model": GraphVisualizationResponse},
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
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def visualize_graph(
    collection_name: str = Path(
        ...,
        description="시각화할 컬렉션 이름입니다.",
        examples=["test"]
    ),
    limit: Optional[int] = Query(
        None,
        description="시각화할 최대 노드 개수입니다. (기본값: 전체)",
        examples=[100]
    ),
    min_mention_count: int = Query(
        1, ge=1,
        description="포함할 최소 언급 횟수입니다. (기본값: 1)",
        examples=[1, 5]
    ),
    entity_types: Optional[str] = Query(
        None,
        description="포함할 엔티티 유형의 콤마(,) 구분 리스트입니다. (기본값: 전체)",
        examples=["PERSON,ORGANIZATION"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    graphrag_service: GraphRAGService = Depends(get_graphrag_service),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 컬렉션 존재 확인
    query = (db_models.Collection.select()
             .where(db_models.Collection.name == collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    if not collection:
        return NotFoundResponse(
            message="컬렉션을 찾을 수 없습니다.",
            target=f"collection_name={collection_name}"
        )
    
    if not access_scope.can_access_scope(user_info, collection.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    # 엔티티 타입 파싱
    entity_type_list = None
    if entity_types:
        entity_type_list = [t.strip() for t in entity_types.split(",") if t.strip()]
    
    # 시각화 데이터 조회
    data = graphrag_service.get_visualization_data(
        collection_name=collection_name,
        limit=limit,
        min_mention_count=min_mention_count,
        entity_types=entity_type_list
    )
    
    return GraphVisualizationResponse(
        collection_name=collection_name,
        nodes=[GraphNode(**node) for node in data["nodes"]],
        edges=[GraphEdge(**edge) for edge in data["edges"]],
        communities=[CommunityInfo(**comm) for comm in data["communities"]],
        stats=data["stats"]
    )

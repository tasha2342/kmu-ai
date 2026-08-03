import uuid
import orjson

from urllib.parse import quote

from logging import Logger

from datetime import datetime

from typing import Optional

from peewee import fn

from fastapi import APIRouter, Depends, Request, BackgroundTasks, UploadFile, File, Form, Query, Path
from fastapi.responses import ORJSONResponse, StreamingResponse

from botocore.exceptions import ClientError

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    InternalServerErrorException,
)
from app.payloads.v1.document import (
    SearchDocumentPayload,
    DocumentFileData,
)
from app.responses.base import BaseMessageResponse, BaseListResponse
from app.responses.v1.document import (
    DocumentInfo,
    UploadDocumentResponse,
    SearchResult,
    SearchDocumentResponse,
    ParsedPageContent,
    ParseDocumentResponse,
    DocumentChunk,
    DocumentContentResponse,
)
from app.responses.exception import (
    BadRequestResponse,
    ForbiddenResponse,
    NotFoundResponse,
    InternalServerErrorResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import (
    BadRequestError,
    NotFoundError,
)
from app.models.api.common import OrderBy
from app.models.enum import ModelType, ModelStatus, DocumentStatus
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.s3 import get_s3_manager, S3Manager
from app.utils.vector_store import get_vector_store_manager, VectorStoreManager
from app.utils.doc_parser import parse_document
from app.utils.text_chunker import chunk_text
from app.utils.litellm import create_embedding
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.access_scope as access_scope
import app.utils.common as util


router = APIRouter()


@router.get("/list", summary="문서 목록 조회",
    description="등록된 문서 목록을 페이징 처리하여 조회합니다.",
    responses={
        200: {"description": "문서 목록을 반환합니다.", "model": BaseListResponse[DocumentInfo]},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_document_list(
    request: Request,
    page: int = Query(
        1, ge=1,
        description="조회할 페이지 번호입니다.",
        examples=[1, 2, 3, 4, 5]
    ),
    count: int = Query(
        10, ge=0, le=100,
        description="페이지당 항목 수입니다. (최대 100개, 0이면 전체 조회)",
        examples=[10, 20, 50, 100]
    ),
    order_by: Optional[OrderBy] = Query(
        OrderBy.CREATED_AT_ASC,
        description="모델 목록 정렬 방식입니다.",
        examples=list(OrderBy)
    ),
    collection_name: Optional[str] = Query(
        None,
        description="컬렉션 이름으로 필터링합니다.",
        examples=["test"]
    ),
    status: Optional[DocumentStatus] = Query(
        None,
        description="문서 상태로 필터링합니다.",
        examples=list(DocumentStatus)
    ),
    search: Optional[str] = Query(
        None,
        description="파일명으로 검색합니다.",
        examples=["report"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    if collection_name:
        coll_query = (db_models.Collection.select()
                      .where(db_models.Collection.name == collection_name))
        coll = await db_manager.select_item(coll_query)
        if coll is not None and not access_scope.can_access_scope(user_info, coll.access_scope):
            return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    # 기본 쿼리
    query = db_models.Document.select()
    count_query = db_models.Document.select(fn.COUNT(db_models.Document.id).alias("count"))
    
    # 필터 조건
    conditions = []
    
    if not access_scope.user_has_admin_access(user_info):
        names_query = db_models.Collection.select(db_models.Collection.name)
        scope_expr = access_scope.peewee_access_scope_filter(
            db_models.Collection.access_scope, user_info
        )
        if scope_expr is not None:
            names_query = names_query.where(scope_expr)
        name_rows = await db_manager.execute_query(names_query)
        allowed_names = [r.name for r in name_rows]
        if not allowed_names:
            return BaseListResponse[DocumentInfo](
                total_pages=1,
                total_count=0,
                items=[]
            )
        conditions.append(db_models.Document.collection_name.in_(allowed_names))
    
    if collection_name:
        conditions.append(db_models.Document.collection_name == collection_name)
    if status:
        conditions.append(db_models.Document.status == status)
    if search:
        conditions.append(db_models.Document.file_name.contains(search))
    
    # 조건 적용
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)
    
    # 정렬
    if order_by == OrderBy.CREATED_AT_ASC:
        query = query.order_by(db_models.Document.created_at.asc())
    elif order_by == OrderBy.CREATED_AT_DESC:
        query = query.order_by(db_models.Document.created_at.desc())
    elif order_by == OrderBy.UPDATED_AT_ASC:
        query = query.order_by(db_models.Document.updated_at.asc())
    elif order_by == OrderBy.UPDATED_AT_DESC:
        query = query.order_by(db_models.Document.updated_at.desc())
    
    # 페이징
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)
    
    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0
    
    # 목록 조회
    documents: list[db_items.Document] = await db_manager.select_items(query)
    
    # 총 페이지 수
    if count > 0:
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    else:
        total_pages = 1
    
    return BaseListResponse[DocumentInfo](
        total_pages=total_pages,
        total_count=total_count,
        items=[
            DocumentInfo(
                **doc.model_dump(),
                file_url=f"{util.get_server_url(request)}/v1/document/download/{doc.id}"
            )
            for doc in documents
        ]
    )

@router.post("/parse", summary="문서 파싱",
    description="업로드된 문서 파일을 Doc Parser로 파싱하여 페이지별 텍스트 내용과 메타데이터를 반환합니다.",
    responses={
        200: {"description": "문서 파싱 결과를 반환합니다.", "model": ParseDocumentResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "empty_file_upload": {
                            "summary": "빈 파일 업로드",
                            "value": {
                                "message": "파일이 비어있습니다.",
                                "target": "file={file}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def parse_document_endpoint(
    file: UploadFile = File(
        ...,
        description="파싱할 문서 파일입니다."
    ),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    file_name = file.filename
    file_data = await file.read()
    file_size = len(file_data)

    if file_size == 0:
        return BadRequestResponse(
            message="파일이 비어있습니다.",
            target=f"file={file_name}"
        )

    parsed_result = await parse_document(file_data, file_name)
    if not parsed_result:
        return InternalServerErrorResponse(
            message="문서 파싱에 실패했습니다."
        )

    contents = [
        ParsedPageContent(
            page=item.get("page", idx + 1),
            content=item.get("content", "")
        )
        for idx, item in enumerate(parsed_result.get("contents", []))
    ]

    logger.debug(f"문서 파싱이 완료되었습니다. (file_name={file_name}, total_pages={parsed_result.get('total_pages', 0)})")

    return ParseDocumentResponse(
        file_name=file_name,
        total_pages=parsed_result.get("total_pages", 0),
        contents=contents,
        metadata=parsed_result.get("metadata", {})
    )

@router.post("/upload", summary="문서 업로드",
    description="문서를 업로드하고 저장합니다.",
    responses={
        200: {"description": "문서 업로드 결과를 반환합니다.", "model": UploadDocumentResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "empty_file_upload": {
                            "summary": "빈 파일 업로드",
                            "value": {
                                "message": "파일이 비어있습니다.",
                                "target": "file={file}"
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
                        "not_found_collection": {
                            "summary": "등록되지 않은 컬렉션",
                            "value": {
                                "message": "컬렉션을 찾을 수 없습니다.",
                                "target": "collection_name={collection_name}"
                            }
                        },
                        "not_found_model": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "컬렉션의 임베딩 모델을 찾을 수 없습니다.",
                                "target": "embedding_model={embedding_model}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(
        ...,
        description="업로드할 문서 파일입니다."
    ),
    background: bool = Form(
        False,
        description=(
            "백그라운드 처리 여부입니다.  \n"
            "`true`로 설정하면 업로드 후 즉시 응답하며, 문서 처리는 백그라운드에서 진행됩니다.  \n"
            "`false`로 설정하면 문서 처리가 완료될 때까지 대기합니다.  \n"
            "문서 처리 상태는 문서 목록에서 확인할 수 있습니다."
        ),
        examples=[True, False]
    ),
    collection_name: str = Form(
        ...,
        description=(
            "컬렉션 이름입니다.  \n"
            "동일한 컬렉션에 속한 문서들끼리 검색됩니다.  \n"
            "컬렉션에 설정된 임베딩 모델을 사용합니다."
        ),
        examples=["test"]
    ),
    chunk_size: int = Form(
        1000, ge=100,
        description="청크의 목표 크기(문자 수)입니다. (기본값: 1000)",
        examples=[500, 1000]
    ),
    chunk_overlap: int = Form(
        100, ge=0,
        description="청크 간 오버랩 크기(문자 수)입니다. (기본값: 100)",
        examples=[50, 100]
    ),
    file_data: Optional[str] = Form(
        None,
        description=(
            "사전 파싱된 문서 데이터(JSON 문자열)입니다.  \n"
            "제공하면 Doc Parser를 통한 파싱 과정을 건너뛰고 해당 데이터를 그대로 사용합니다.  \n"
            "형식: `{\"contents\": [{\"page\": 1, \"content\": \"...\"}], \"metadata\": {...}}`"
        )
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    s3_manager: S3Manager = Depends(get_s3_manager),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
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
    
    # 컬렉션의 임베딩 모델 확인
    query = (db_models.Model.select()
             .where(db_models.Model.name == collection.embedding_model)
             .where(db_models.Model.model_type == ModelType.EMBEDDING.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="컬렉션의 임베딩 모델을 찾을 수 없습니다.",
            target=f"embedding_model={collection.embedding_model}"
        )
    
    # file_data JSON 파싱 (제공된 경우)
    parsed_file_data: Optional[DocumentFileData] = None
    if file_data:
        try:
            parsed_file_data = DocumentFileData.model_validate(orjson.loads(file_data))
        except (orjson.JSONDecodeError, ValueError) as exc:
            return BadRequestResponse(
                message=f"file_data 형식이 올바르지 않습니다: {exc}",
                target=f"file_data={file_data}"
            )

    # 파일 읽기
    file_name = file.filename
    raw_file_data = await file.read()
    file_size = len(raw_file_data)
    if file_size == 0:
        return BadRequestResponse(
            message="파일이 비어있습니다.",
            target=f"file={file_name}"
        )
    
    # 파일 타입 추출
    file_type = file_name.split(".")[-1].lower() if "." in file_name else "unknown"
    
    # 파일 저장
    timestamp = int(datetime.now().timestamp())
    ext = f".{file_type}" if file_type != "unknown" else ""
    base_name = file_name[: len(file_name) - len(ext)] if ext else file_name
    safe_base = base_name[:100]
    safe_file_name = f"{safe_base}{ext}"
    object_name = f"documents/{collection_name}/{timestamp}_{safe_file_name}"
    
    try:
        s3_manager.upload_file(
			file_data=raw_file_data,
			object_name=object_name,
			content_type=file.content_type or "application/octet-stream"
		)
    except Exception:
        logger.exception("파일 업로드 중 오류가 발생했습니다.")
        return InternalServerErrorResponse(
			message="파일 업로드 중 오류가 발생했습니다."
		)
    
    # 문서 정보 저장
    query = db_models.Document.insert(
        collection_name=collection_name,
        user_name=user_info.username,
        file_name=file_name,
        file_path=object_name,
        file_size=file_size,
        file_type=file_type,
        status=DocumentStatus.PROCESSING
    )
    result = await db_manager.execute_query(query)
    document_id = result
    
    logger.debug(f"문서 업로드가 시작되었습니다. (document_id={document_id})")
    
    async def _process_document() -> ORJSONResponse:
        try:
            # 문서 파싱 (file_data가 제공된 경우 파싱 생략)
            if parsed_file_data:
                contents = [item.model_dump() for item in parsed_file_data.contents]
                doc_metadata = parsed_file_data.metadata
            else:
                parsed_result = await parse_document(raw_file_data, file_name)
                if not parsed_result:
                    raise InternalServerErrorException("문서 파싱에 실패했습니다.")
                
                contents = parsed_result["contents"]
                doc_metadata = parsed_result["metadata"]
            
            # 페이지별 텍스트 청킹
            all_chunks = []
            for content_item in contents:
                page_content = content_item.get("content", "")
                page_number = content_item.get("page", 1)
                if not page_content or not page_content.strip():
                    continue
                
                page_metadata = {
                    **doc_metadata,
                    "page": page_number,
                }
                page_chunks = chunk_text(
                    text=page_content,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    metadata=page_metadata
                )
                all_chunks.extend(page_chunks)
            
            # 전체 청크 인덱스 재정렬
            chunks = all_chunks
            total_chunks = len(chunks)
            for i, chunk in enumerate(chunks):
                chunk["metadata"]["chunk_index"] = i
                chunk["metadata"]["total_chunks"] = total_chunks
            
            if not chunks:
                raise InternalServerErrorException("청크 생성에 실패했습니다.")
            
            try:
                # 배치로 임베딩 생성
                texts = [chunk["text"] for chunk in chunks]
                embeddings_response = await create_embedding(model, user_info, texts)

                embeddings = [data["embedding"] for data in embeddings_response.data]
            except Exception:
                logger.exception(f"문서 임베딩 중 오류가 발생했습니다. (document_id={document_id})")
                raise InternalServerErrorException("문서 임베딩 중 오류가 발생했습니다.")
            
            try:
                # 벡터 저장
                points = []
                for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                    point = {
                        "id": str(uuid.uuid4()),
                        "vector": embedding,
                        "payload": {
                            "document_id": document_id,
                            "chunk_index": i,
                            "content": chunk["text"],
                            "file_name": file_name,
                            **chunk["metadata"]
                        }
                    }
                    points.append(point)
                
                await vector_store.upsert_points_async(collection_name, points)
            except Exception:
                logger.exception(f"문서 벡터 저장 중 오류가 발생했습니다. (document_id={document_id})")
                raise InternalServerErrorException("문서 저장 중 오류가 발생했습니다.")

            # DB 업데이트 (상태: completed)
            query = (db_models.Document.update(
                chunk_count=len(chunks),
                metadata=doc_metadata,
                status=DocumentStatus.COMPLETED,
                updated_at=util.get_now()
            ).where(db_models.Document.id == document_id))
            await db_manager.execute_query(query)
            
            logger.debug(f"문서가 성공적으로 처리되었습니다. (document_id={document_id})")
            
            return UploadDocumentResponse(
				document_id=document_id,
				file_name=file.filename,
				collection_name=collection_name,
				chunk_count=len(chunks)
			)
        except InternalServerErrorException as exc:
			# DB 업데이트 (상태: error)
            query = (db_models.Document.update(
                status=DocumentStatus.ERROR,
                error_message=str(exc),
                updated_at=util.get_now()
            ).where(db_models.Document.id == document_id))
            await db_manager.execute_query(query)
            
            return InternalServerErrorResponse(
				message=str(exc)
			)
        except Exception:
            logger.exception("문서 처리 중 오류가 발생했습니다.")
            
            # DB 업데이트 (상태: error)
            query = (db_models.Document.update(
                status=DocumentStatus.ERROR,
                error_message="서버 내부 오류가 발생했습니다.",
                updated_at=util.get_now()
            ).where(db_models.Document.id == document_id))
            await db_manager.execute_query(query)
            
            return InternalServerErrorResponse(
				message="서버 내부 오류가 발생했습니다."
			)

	# 백그라운드에서 처리
    if background:
        background_tasks.add_task(_process_document)
        return UploadDocumentResponse(
			document_id=document_id,
			file_name=file.filename,
			collection_name=collection_name,
			chunk_count=0
		)
    
    return await _process_document()

@router.delete("/delete/{doc_id}", summary="문서 삭제",
    description=(
        "문서를 삭제합니다.  \n"
        "저장된 파일도 함께 삭제됩니다."
    ),
    responses={
        200: {"description": "문서 삭제 결과를 반환합니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 문서",
                            "value": {
                                "message": "문서를 찾을 수 없습니다.",
                                "target": "doc_id={doc_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_document(
    doc_id: int = Path(
        ...,
        description="삭제할 문서 ID입니다.",
        examples=[1, 2, 3]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    s3_manager: S3Manager = Depends(get_s3_manager),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 문서 조회
    query = (db_models.Document.select()
             .where(db_models.Document.id == doc_id))
    document: Optional[db_items.Document] = await db_manager.select_item(query)
    if not document:
        return NotFoundResponse(
            message=f"문서를 찾을 수 없습니다.",
            target=f"doc_id={doc_id}"
        )
    
    # 벡터 삭제
    await vector_store.delete_points_by_document_id_async(document.collection_name, doc_id)
    
    try:
        # 파일 삭제
        s3_manager.delete_file(document.file_path)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code != "404":
            raise exc
    
    # DB 삭제
    query = (db_models.Document.delete()
             .where(db_models.Document.id == doc_id))
    await db_manager.execute_query(query)
    
    logger.debug(f"문서가 삭제되었습니다. (document_id={doc_id})")
    
    return BaseMessageResponse(
        message="문서가 성공적으로 삭제되었습니다."
    )

@router.post("/search", summary="문서 검색",
    description=(
        "벡터 유사도 기반으로 문서를 검색합니다.  \n"
        "컬렉션에 설정된 임베딩 모델을 사용하여 검색합니다."
    ),
    responses={
        200: {"description": "검색 결과를 반환합니다.", "model": SearchDocumentResponse},
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
                        "not_found_model": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "컬렉션의 임베딩 모델을 찾을 수 없습니다.",
                                "target": "embedding_model={embedding_model}"
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
async def search_documents(
    payload: SearchDocumentPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
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
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="컬렉션의 임베딩 모델을 찾을 수 없습니다.",
            target=f"embedding_model={collection.embedding_model}"
        )
    
    # 쿼리 임베딩 생성
    embeddings_response = await create_embedding(model, user_info, [payload.query])

    query_vector = embeddings_response.data[0]["embedding"]
    
    # 문서 검색
    search_results = await vector_store.search_async(
        collection_name=payload.collection_name,
        query_vector=query_vector,
        limit=payload.top_k,
        score_threshold=payload.score_threshold,
    )
    
    # 결과 포맷팅
    results = [
        SearchResult(
            document_id=result["payload"].get("document_id"),
            file_name=result["payload"].get("file_name"),
            chunk_index=result["payload"].get("chunk_index"),
            content=result["payload"].get("content"),
            page=result["payload"].get("page"),
            score=result["score"],
            metadata={
                k: v for k, v in result["payload"].items()
                if k not in ["document_id", "file_name", "chunk_index", "content", "page"]
            }
        )
        for result in search_results
    ]
    
    return SearchDocumentResponse(
        query=payload.query,
        collection_name=payload.collection_name,
        results=results,
        total_results=len(results)
    )

@router.get("/content/{doc_id}", summary="문서 전체 내용 조회",
    description=(
        "저장된 문서의 전체 텍스트 내용을 청크 순서대로 반환합니다.  \n"
        "벡터 저장소에 저장된 청크를 `chunk_index` 기준으로 정렬하여 반환합니다."
    ),
    responses={
        200: {"description": "문서 전체 내용을 반환합니다.", "model": DocumentContentResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found_doc": {
                            "summary": "등록되지 않은 문서",
                            "value": {
                                "message": "문서를 찾을 수 없습니다.",
                                "target": "doc_id={doc_id}"
                            }
                        },
                        "not_found_content": {
                            "summary": "문서 내용 없음",
                            "value": {
                                "message": "문서 내용을 찾을 수 없습니다.",
                                "target": "doc_id={doc_id}"
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
async def get_document_content(
    doc_id: int = Path(
        ...,
        description="조회할 문서 ID입니다.",
        examples=[1, 2, 3]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    vector_store: VectorStoreManager = Depends(get_vector_store_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 문서 조회
    query = (db_models.Document.select()
             .where(db_models.Document.id == doc_id))
    document: Optional[db_items.Document] = await db_manager.select_item(query)
    if not document:
        return NotFoundResponse(
            message="문서를 찾을 수 없습니다.",
            target=f"doc_id={doc_id}"
        )
    
    coll_query = (db_models.Collection.select()
                  .where(db_models.Collection.name == document.collection_name))
    coll = await db_manager.select_item(coll_query)
    if coll is not None and not access_scope.can_access_scope(user_info, coll.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")

    # 벡터 저장소에서 청크 조회
    raw_points = await vector_store.get_points_by_document_id_async(
        collection_name=document.collection_name,
        document_id=doc_id
    )

    if not raw_points:
        return NotFoundResponse(
            message="문서 내용을 찾을 수 없습니다.",
            target=f"doc_id={doc_id}"
        )

    # chunk_index 기준으로 정렬
    raw_points.sort(key=lambda p: p.get("chunk_index", 0))

    chunks = [
        DocumentChunk(
            chunk_index=point.get("chunk_index", idx),
            content=point.get("content", ""),
            page=point.get("page"),
            metadata={
                k: v for k, v in point.items()
                if k not in ["document_id", "file_name", "chunk_index", "content", "page",
                             "total_chunks"]
            } or None
        )
        for idx, point in enumerate(raw_points)
    ]

    logger.debug(f"문서 전체 내용이 조회되었습니다. (document_id={doc_id}, total_chunks={len(chunks)})")

    return DocumentContentResponse(
        document_id=doc_id,
        file_name=document.file_name,
        collection_name=document.collection_name,
        total_chunks=len(chunks),
        metadata=document.metadata,
        chunks=chunks
    )

@router.get("/download/{doc_id}", summary="문서 다운로드",
    description="문서 파일을 다운로드합니다.",
    responses={
        200: {
            "description": "문서 파일을 반환합니다.",
            "content": {
                "application/octet-stream": {
                    "schema": {
                        "type": "string",
                        "format": "binary"
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
                        "not_found_doc": {
                            "summary": "등록되지 않은 문서",
                            "value": {
                                "message": "문서를 찾을 수 없습니다.",
                                "target": "doc_id={doc_id}"
                            }
                        },
                        "not_found_file": {
                            "summary": "문서의 파일을 찾을 수 없음",
                            "value": {
                                "message": "문서의 파일을 찾을 수 없습니다.",
                                "target": "doc_id={doc_id}"
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
async def download_document(
    doc_id: int = Path(
        ...,
        description="다운로드할 문서 ID입니다.",
        examples=[1, 2, 3]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    s3_manager: S3Manager = Depends(get_s3_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 문서 조회
    query = (db_models.Document.select()
             .where(db_models.Document.id == doc_id))
    document: Optional[db_items.Document] = await db_manager.select_item(query)
    if not document:
        return NotFoundResponse(
            message="문서를 찾을 수 없습니다.",
            target=f"doc_id={doc_id}"
        )
    
    coll_query = (db_models.Collection.select()
                  .where(db_models.Collection.name == document.collection_name))
    coll = await db_manager.select_item(coll_query)
    if coll is not None and not access_scope.can_access_scope(user_info, coll.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    try:
        # 파일 정보 조회
        file_info = s3_manager.get_file_info(document.file_path)
        # 파일 다운로드
        file_data = s3_manager.download_file(document.file_path)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code == "404":
            return NotFoundResponse(
                message="문서의 파일을 찾을 수 없습니다.",
                target=f"doc_id={doc_id}"
            )
        raise exc
    
    # 메타데이터에서 Content-Type 가져오기
    content_type = file_info.get("content_type", "application/octet-stream")
    
    # 파일명 인코딩
    encoded_filename = quote(document.file_name)
    
    return StreamingResponse(
        iter([file_data]),
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }
    )

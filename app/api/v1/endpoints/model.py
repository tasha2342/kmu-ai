import orjson

from logging import Logger

from typing import Optional

from peewee import fn, IntegrityError

from fastapi import APIRouter, Depends, Path, Query, BackgroundTasks

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
)
from app.payloads.v1.model import (
    AddModelPayload,
    UpdateModelPayload,
    RunModelPayload,
)
from app.responses.base import BaseMessageResponse, BaseMessageIdResponse, BaseListResponse
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
from app.models.api.common import OrderBy
from app.models.enum import ModelProvider, ModelType, ModelStatus
from app.models.cost import TextGenerationCost, EmbeddingCost, RerankCost
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.redis import get_redis
from app.utils.litellm import validate_model
from app.utils.local_model import LocalModel
from app.utils.node_client import NodeClient
from app.utils.locker import lock, is_locked
import app.utils.embedder as embedder
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


@router.get("/list", summary="모델 목록 조회",
    description=(
        "등록된 모델 목록을 페이징 처리하여 조회합니다.  \n"
        "모델의 기본 정보, 제공자, 상태를 페이지 단위로 반환합니다."
    ),
    responses={
        200: {"description": "모델 목록을 반환합니다.", "model": BaseListResponse[db_items.Model]},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_model_list(
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
    provider: Optional[ModelProvider] = Query(
        None,
        description="모델 제공자로 필터링합니다.",
        examples=list(ModelProvider)
    ),
    model_type: Optional[ModelType] = Query(
        None,
        description="모델 타입으로 필터링합니다.",
        examples=list(ModelType)
    ),
    status_filter: Optional[ModelStatus] = Query(
        None,
        alias="status",
        description="모델 상태로 필터링합니다.",
        examples=list(ModelStatus)
    ),
    search: Optional[str] = Query(
        None,
        description="모델명 또는 모델 ID로 검색합니다.",
        examples=["gpt-4", "claude"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 기본 쿼리 생성
    query = db_models.Model.select()
    count_query = db_models.Model.select(fn.COUNT(db_models.Model.name).alias("count"))
    
    # 필터 조건 추가
    conditions = []
    if provider is not None:
        conditions.append(db_models.Model.provider == provider.value)
    if model_type is not None:
        conditions.append(db_models.Model.model_type == model_type.value)
    if status_filter is not None:
        conditions.append(db_models.Model.status == status_filter.value)
    if search is not None:
        conditions.append((
            (db_models.Model.name.contains(search)) |
            (db_models.Model.model_id.contains(search))
        ))
    
    # 조건이 있으면 WHERE 절 추가
    if conditions:
        combined_condition = conditions[0]
        for condition in conditions[1:]:
            combined_condition = combined_condition & condition
        query = query.where(combined_condition)
        count_query = count_query.where(combined_condition)
    
    # 정렬 적용
    if order_by == OrderBy.CREATED_AT_ASC:
        query = query.order_by(db_models.Model.created_at.asc())
    elif order_by == OrderBy.CREATED_AT_DESC:
        query = query.order_by(db_models.Model.created_at.desc())
    elif order_by == OrderBy.UPDATED_AT_ASC:
        query = query.order_by(db_models.Model.updated_at.asc())
    elif order_by == OrderBy.UPDATED_AT_DESC:
        query = query.order_by(db_models.Model.updated_at.desc())

    # 페이징 처리 (count가 0이면 전체 조회)
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)
    
    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0
    
    # 목록 조회
    models: list[db_items.Model] = await db_manager.select_items(query)

    # 총 페이지 수 계산
    if count > 0:
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    else:
        total_pages = 1
    
    return BaseListResponse[db_items.Model](
        total_pages=total_pages,
        total_count=total_count,
        items=models
    )

@router.get("/info/{model_name:path}", summary="모델 상세 조회",
    description="특정 모델의 상세 정보를 조회합니다.",
    responses={
        200: {"description": "모델 정보를 반환합니다.", "model": db_items.Model},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "model_name={model_name}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_model_info(
    model_name: str = Path(
        ...,
        description="조회할 모델명입니다.",
        examples=["gpt-4o-mini"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 모델 조회
    query = (db_models.Model.select()
                .where(db_models.Model.name == model_name))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"name={model_name}"
        )

    return model

@router.post("/add", summary="모델 추가",
    description="새로운 모델을 추가합니다.",
    responses={
        200: {"description": "모델이 추가되었습니다.", "model": BaseMessageIdResponse[str]},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "unsupported_model": {
                            "summary": "지원하지 않는 모델",
                            "value": {
                                "message": "지원하지 않는 모델입니다.",
                                "target": "model_id={model_id}"
                            }
                        },
                        "invalid_api_key": {
                            "summary": "잘못된 API Key",
                            "value": {
                                "message": "모델 제공자 서버의 API Key가 올바르지 않습니다.",
                                "target": "api_key={api_key}"
                            }
                        },
                        "model_not_found": {
                            "summary": "모델을 찾을 수 없음",
                            "value": {
                                "message": "모델 제공자 서버에서 모델을 찾을 수 없습니다.",
                                "target": "model_id={model_id}"
                            }
                        },
                        "connection_error": {
							"summary": "모델 제공자 서버 연결 오류",
							"value": {
								"message": "모델 제공자 서버에 연결할 수 없습니다.",
								"target": "api_base={api_base}"
							}
						},
                        "permission_denied": {
							"summary": "접근 권한 없음",
							"value": {
								"message": "해당 모델에 대한 접근 권한이 없습니다.",
								"target": "provider={provider}, model_id={model_id}"
							}
						},
                        "invalid_cost": {
                            "summary": "잘못된 cost 형식",
                            "value": {
                                "message": "'cost' 형식이 모델 타입에 맞지 않습니다.",
                                "target": "cost={cost}"
                            }
                        }
                    }
                }
            }
        },
        409: {
            "description": "이미 등록된 항목입니다.", 
            "model": ConflictError,
            "content": {
                "application/json": {
                    "examples": {
                        "already_added": {
                            "summary": "이미 등록된 모델",
                            "value": {
                                "message": "이미 등록된 모델입니다.",
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
async def add_model(
    background_tasks: BackgroundTasks,
    payload: AddModelPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 모델 검증
    validation_result = await validate_model(
        provider=payload.provider,
        model_name=payload.name,
        model_id=payload.model_id,
        model_type=payload.model_type,
        api_key=payload.api_key,
        api_base=payload.api_base
    )
    
    if not validation_result.success:
        if validation_result.error_type == "unknown_error":
            return InternalServerErrorResponse(
				message=validation_result.message
			)
        
        # 오류 타입에 따른 target 설정
        target_keys_map = {
            "unsupported_model": ["model_id"],
            "invalid_api_key": ["api_key"],
            "model_not_found": ["model_id"],
            "connection_error": ["api_base"],
            "permission_denied": ["provider", "model_id"],
        }
        target_key = target_keys_map.get(validation_result.error_type, ["model"])
        
        target_str = ", ".join(f"{key}={getattr(payload, key)}" for key in target_key)
        
        return BadRequestResponse(
            message=validation_result.message,
            target=target_str
        )
    
    try:
        # 로컬 모델인 경우 백그라운드에서 모델 다운로드
        if payload.provider == ModelProvider.LOCAL:
            model_status = ModelStatus.DOWNLOADING
        else:
            # 원격 모델은 바로 로드 상태로 설정
            model_status = ModelStatus.RUNNING
        
        api_base = None
        if payload.provider == ModelProvider.OPENAI_COMPATIBLE:
            api_base = payload.api_base
        
        # cost 검증
        cost_data = None
        if payload.cost is not None:
            try:
                cost_models = {
                    ModelType.TEXT_GENERATION: TextGenerationCost,
                    ModelType.EMBEDDING: EmbeddingCost,
                    ModelType.RERANK: RerankCost,
                }
                cost_model_cls = cost_models.get(payload.model_type)
                cost_data = cost_model_cls.model_validate(payload.cost).model_dump()
            except Exception:
                return BadRequestResponse(
                    message="'cost' 형식이 모델 타입에 맞지 않습니다.",
                    target=f"cost={payload.cost}"
                )
        
        # 모델 생성
        query = db_models.Model.insert(
            name=payload.name,
            provider=payload.provider.value,
            model_id=payload.model_id,
            model_type=payload.model_type.value,
            api_base=api_base,
            api_key=payload.api_key,
            description=payload.description,
            cost=cost_data,
            status=model_status.value,
            config=payload.config
        )
        await db_manager.execute_query(query)
    except IntegrityError:
        return ConflictResponse(
			message="이미 등록된 모델입니다.",
			target=f"name={payload.name}"
		)
    
    if payload.provider == ModelProvider.LOCAL:
        async def _download_model():
            local_model = LocalModel(model_id=payload.model_id, api_key=payload.api_key)
            try:
                if await local_model.is_downloaded():
                    logger.info(f"모델이 이미 다운로드되어 있어 재사용합니다. (name={payload.name}, model_id={payload.model_id})")
                else:
                    logger.info(f"모델을 다운로드 중입니다. (name={payload.name}, model_id={payload.model_id})")
                    await local_model.download_model()
                    logger.info(f"모델이 다운로드되었습니다. (name={payload.name}, model_id={payload.model_id})")
                
                # 상태 업데이트
                update_query = (db_models.Model.update(
                    status=ModelStatus.STOPPED.value,
                    error_message=None,
                    updated_at=util.get_now()
                ).where(db_models.Model.name == payload.name))
                await db_manager.execute_query(update_query)
            except Exception as exc:
                await local_model.clean_model()
                logger.exception(f"모델 다운로드 중 오류가 발생했습니다. (name={payload.name})")
                
                # 상태 업데이트
                update_query = (db_models.Model.update(
                    status=ModelStatus.ERROR.value,
                    error_message=f"모델 다운로드 중 오류: {str(exc)}",
                    updated_at=util.get_now()
                ).where(db_models.Model.name == payload.name))
                await db_manager.execute_query(update_query)
        
        background_tasks.add_task(_download_model)
        
        return BaseMessageIdResponse[str](
            message="모델 다운로드가 시작되었습니다.",
            id=payload.name
        )
    else:
        logger.info(f"모델이 추가되었습니다. (name={payload.name})")
        
        return BaseMessageIdResponse[str](
            message="모델이 추가되었습니다.",
            id=payload.name
        )

@router.patch("/update/{model_name:path}", summary="모델 수정",
    description="모델 정보를 수정합니다.",
    responses={
        200: {"description": "모델 정보가 수정되었습니다.", "model": BaseMessageResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_api_key": {
                            "summary": "잘못된 API Key",
                            "value": {
                                "message": "모델 제공자 서버의 API Key가 올바르지 않습니다.",
                                "target": "api_key={api_key}"
                            }
                        },
                        "model_not_found": {
                            "summary": "모델을 찾을 수 없음",
                            "value": {
                                "message": "모델 제공자 서버에서 모델을 찾을 수 없습니다.",
                                "target": "model_id={model_id}"
                            }
                        },
                        "connection_error": {
							"summary": "모델 제공자 서버 연결 오류",
							"value": {
								"message": "모델 제공자 서버에 연결할 수 없습니다.",
								"target": "api_base={api_base}"
							}
						},
                        "permission_denied": {
							"summary": "접근 권한 없음",
							"value": {
								"message": "해당 모델에 대한 접근 권한이 없습니다.",
								"target": "provider={provider}, model_id={model_id}"
							}
						},
                        "invalid_cost": {
                            "summary": "잘못된 cost 형식",
                            "value": {
                                "message": "'cost' 형식이 모델 타입에 맞지 않습니다.",
                                "target": "cost={cost}"
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
							"summary": "등록되지 않은 모델",
							"value": {
								"message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
								"target": "model_name={model_name}"
							}
						}
					}
				}
			}
		},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_model(
    payload: UpdateModelPayload,
    model_name: str = Path(
        ...,
        description="수정할 모델명입니다.",
        examples=["gpt-4o-mini"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 기존 모델 조회
    query = (db_models.Model.select()
                .where(db_models.Model.name == model_name))
    existing_model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not existing_model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"model_name={model_name}"
        )
    
    # 업데이트할 값 확인
    update_values = payload.model_dump(exclude_unset=True, exclude_none=True)

    if payload.cost is not None:
        try:
            cost_models = {
                ModelType.TEXT_GENERATION: TextGenerationCost,
                ModelType.EMBEDDING: EmbeddingCost,
                ModelType.RERANK: RerankCost,
            }
            cost_model_cls = cost_models.get(existing_model.model_type)
            update_values["cost"] = cost_model_cls.model_validate(payload.cost).model_dump()
        except Exception:
            return BadRequestResponse(
                message="'cost' 형식이 모델 타입에 맞지 않습니다.",
                target=f"cost={payload.cost}"
            )
    
    # Local 모델이 아니고 API Base, API Key 중 하나라도 변경되면 검증 수행
    needs_validation = any(key in update_values for key in ["api_base", "api_key"])
    if existing_model.provider != ModelProvider.LOCAL and needs_validation:
        # 검증용 파라미터 구성 (변경된 값 우선, 없으면 기존 값 사용)
        provider = update_values.get("provider", existing_model.provider)
        model_id = update_values.get("model_id", existing_model.model_id)
        model_type = update_values.get("model_type", existing_model.model_type)
        api_key = update_values.get("api_key", existing_model.api_key)
        api_base = update_values.get("api_base", existing_model.api_base)
        
        # Enum 변환
        if isinstance(provider, str):
            provider = ModelProvider(provider)
        if isinstance(model_type, str):
            model_type = ModelType(model_type)
        
        validation_result = await validate_model(
            provider=provider,
            model_name=model_name,
            model_id=model_id,
            model_type=model_type,
            api_key=api_key,
            api_base=api_base
        )
        
        if not validation_result.success:
            if validation_result.error_type == "unknown_error":
                return InternalServerErrorResponse(
					message=validation_result.message
				)
            
            # 오류 타입에 따른 target 설정
            target_keys_map = {
				"unsupported_model": ["model_id"],
                "invalid_api_key": ["api_key"],
                "model_not_found": ["model_id"],
                "connection_error": ["api_base"],
                "permission_denied": ["provider", "model_id"],
			}
            target_key = target_keys_map.get(validation_result.error_type, ["model"])
            
            target_str = ", ".join(f"{key}={getattr(payload, key)}" for key in target_key)
			
            return BadRequestResponse(
                message=validation_result.message,
                target=target_str
            )
    
    update_values["updated_at"] = util.get_now()
    
    # 모델 정보 수정
    update_query = (db_models.Model.update(**update_values)
				.where(db_models.Model.name == model_name))
    await db_manager.execute_query(update_query)
    
    logger.info(f"모델 정보가 수정되었습니다. (model_name={model_name})")
    
    return BaseMessageResponse(message="모델 정보가 수정되었습니다.")

@router.delete("/delete/{model_name:path}", summary="모델 삭제",
    description="모델을 삭제합니다.",
    responses={
        200: {"description": "모델이 삭제되었습니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "model_name={model_name}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_model(
    model_name: str = Path(
        ...,
        description="삭제할 모델명입니다.",
        examples=["gpt-4o-mini"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 모델 조회 (삭제 전 정보 확인)
    query = (db_models.Model.select()
             .where(db_models.Model.name == model_name))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"model_name={model_name}"
        )
    
    if model.provider == ModelProvider.LOCAL:
        # 메모리에 올라가 있으면 먼저 내린다. (가중치 파일을 지운 뒤에도 계속 응답하는 상황 방지)
        await embedder.unload_embedder(model.model_id)

        # 로컬 모델 파일 삭제
        local_model = LocalModel(model_id=model.model_id, api_key=model.api_key)
        await local_model.clean_model()
    
    # 모델 삭제
    delete_query = (db_models.Model.delete()
                    .where(db_models.Model.name == model_name))
    await db_manager.execute_query(delete_query)
    
    logger.info(f"모델이 삭제되었습니다. (model_name={model_name})")
    
    return BaseMessageResponse(message="모델이 삭제되었습니다.")

@router.post("/run/{model_name:path}", summary="모델 실행",
    description="모델을 실행합니다.",
    responses={
        200: {"description": "모델이 실행되었습니다.", "model": BaseMessageResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "lock_acquired": {
                            "summary": "다른 요청이 처리 중임",
                            "value": {
                                "message": "현재 다른 요청을 처리 중입니다. 잠시 후 다시 시도해주세요.",
                                "target": "lock=acquired"
                            }
                        },
                        "already_running": {
                            "summary": "모델이 이미 실행 중임",
                            "value": {
                                "message": "모델이 이미 실행 중입니다.",
                                "target": "status={status}"
                            }
                        },
                        "invalid_status": {
                            "summary": "모델을 실행할 수 없는 상태",
                            "value": {
                                "message": "모델을 실행할 수 없는 상태입니다.",
                                "target": "status={status}"
                            }
                        },
                        "not_downloaded": {
                            "summary": "모델이 다운로드되지 않음",
                            "value": {
                                "message": "모델이 정상적으로 다운로드되지 않았습니다. 모델을 삭제 후 다시 추가해주세요.",
                                "target": "model_id={model_id}"
                            }
                        },
                        "node_not_found": {
                            "summary": "지정한 노드를 찾을 수 없음",
                            "value": {
                                "message": "지정한 노드를 찾을 수 없습니다.",
                                "target": "node_id={node_id}"
                            }
                        },
                        "node_busy": {
                            "summary": "지정한 노드가 바쁨",
                            "value": {
                                "message": "지정한 노드가 현재 바쁩니다. 다른 노드를 선택해주세요.",
                                "target": "node_id={node_id}"
                            }
                        },
                        "device_not_found": {
                            "summary": "지정한 디바이스를 찾을 수 없음",
                            "value": {
                                "message": "지정한 노드에 일부 디바이스가 존재하지 않습니다.",
                                "target": "node_id={node_id}, device_ids={device_ids}"
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
							"summary": "등록되지 않은 모델",
							"value": {
								"message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
								"target": "model_name={model_name}"
							}
						}
					}
				}
			}
		},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def run_model(
    background_tasks: BackgroundTasks,
    payload: RunModelPayload,
    model_name: str = Path(
        ...,
        description="실행할 모델명입니다.",
        examples=["qwen3-32b"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    lock_name = "model_run"
    
    if is_locked(lock_name):
        return BadRequestResponse(
            message="현재 다른 요청을 처리 중입니다. 잠시 후 다시 시도해주세요.",
            target="lock=acquired"
        )
    
    async with lock(lock_name):
        # 모델 조회
        query = (db_models.Model.select()
                .where(db_models.Model.name == model_name))
        model: Optional[db_items.Model] = await db_manager.select_item(query)
        if not model:
            return NotFoundResponse(
                message="등록되지 않은 모델이거나 실행중이 아닙니다.",
                target=f"model_name={model_name}"
            )
        
        # 모델 상태 확인
        if model.status == ModelStatus.RUNNING:
            return BadRequestResponse(
                message="모델이 이미 실행 중입니다.",
                target=f"status={model.status.value}"
            )
        elif model.status != ModelStatus.ERROR and model.status != ModelStatus.STOPPED:
            return BadRequestResponse(
                message="모델을 실행할 수 없는 상태입니다.",
                target=f"status={model.status.value}"
            )
        
        # 원격 모델 처리
        if model.provider != ModelProvider.LOCAL:
            # 상태 업데이트
            update_query = (db_models.Model.update(
                status=ModelStatus.RUNNING.value,
                error_message=None,
                updated_at=util.get_now()
            ).where(db_models.Model.name == model_name))
            await db_manager.execute_query(update_query)
            
            return BaseMessageResponse(
                message="모델이 실행되었습니다."
            )
        
        # 로컬 모델 처리
        
        # 모델이 다운로드 되어있는지 확인
        local_model = LocalModel(model_id=model.model_id, api_key=model.api_key)
        if not await local_model.is_downloaded():
            return BadRequestResponse(
                message="모델이 정상적으로 다운로드되지 않았습니다. 모델을 삭제 후 다시 추가해주세요.",
                target=f"model_id={model.model_id}"
            )
        
        # 컨테이너 내장 임베딩 모델 처리
        # 임베딩 모델은 GPU 노드로 내보내지 않고 이 프로세스에 직접 올린다. (app/utils/embedder.py 참고)
        if model.model_type == ModelType.EMBEDDING:
            async def _load_embedding_model():
                update_query = (db_models.Model.update(
                    status=ModelStatus.LOADING.value,
                    error_message=None,
                    updated_at=util.get_now()
                ).where(db_models.Model.name == model_name))
                await db_manager.execute_query(update_query)

                try:
                    loaded = await embedder.get_embedder(model.model_id)
                    await loaded.load()

                    update_query = (db_models.Model.update(
                        status=ModelStatus.RUNNING.value,
                        error_message=None,
                        api_base=None,
                        updated_at=util.get_now()
                    ).where(db_models.Model.name == model_name))
                    await db_manager.execute_query(update_query)

                    logger.info(f"임베딩 모델을 컨테이너에 로드했습니다. (model_name={model_name}, dim={loaded.dimensions})")
                except Exception:
                    logger.exception(f"임베딩 모델 로드 중 오류가 발생했습니다. (name={model_name})")

                    update_query = (db_models.Model.update(
                        status=ModelStatus.ERROR.value,
                        error_message="모델 실행 중 오류: 임베딩 모델을 로드하지 못했습니다.",
                        updated_at=util.get_now()
                    ).where(db_models.Model.name == model_name))
                    await db_manager.execute_query(update_query)

            background_tasks.add_task(_load_embedding_model)

            return BaseMessageResponse(
                message="모델을 실행합니다."
            )

        if not payload.node_id:
            return BadRequestResponse(
                message="실행할 노드를 지정해주세요.",
                target="node_id=None"
            )

        # 노드가 실행 중인지 확인
        async with get_redis() as redis:
            node_info_str = await redis.get(f"ai-agent-one:nodes:{payload.node_id}")
            if not node_info_str:
                return BadRequestResponse(
                    message="지정한 노드를 찾을 수 없습니다.",
                    target=f"node_id={payload.node_id}"
                )
            
            try:
                node_info: dict = orjson.loads(node_info_str)
            except orjson.JSONDecodeError:
                return InternalServerErrorResponse(
                    message="노드 정보를 확인하는 중 오류가 발생했습니다."
                )
        
        # 노드가 바쁜지 확인
        is_busy: bool = node_info.get("is_busy")
        if is_busy:
            return BadRequestResponse(
                message="지정한 노드가 현재 바쁩니다. 다른 노드를 선택해주세요.",
                target=f"node_id={payload.node_id}"
            )
        
        # 노드에 해당 디바이스가 있는지 확인
        available_devices: list[dict] = node_info.get("devices", [])
        available_device_ids: list[int] = [device.get("device_id") for device in available_devices]
        
        if payload.device_ids and not all(device_id in available_device_ids for device_id in payload.device_ids):
            return BadRequestResponse(
                message="지정한 노드에 일부 디바이스가 존재하지 않습니다.",
                target=f"node_id={payload.node_id}, device_ids={payload.device_ids}"
            )
        
        async def _run_local_model():
            # 상태 업데이트
            update_query = (db_models.Model.update(
                status=ModelStatus.LOADING.value,
                error_message=None,
                updated_at=util.get_now()
            ).where(db_models.Model.name == model_name))
            await db_manager.execute_query(update_query)
            
            try:
                api_base: str = node_info.get("api_base")
                node_client = NodeClient(base_url=api_base, token=user_info.token)
                status_code, response_data = await node_client.run_model(
                    model_name=model_name,
                    device_ids=payload.device_ids,
                    gpu_memory_utilization=payload.gpu_memory_utilization
                )
                
                if status_code != 200:
                    # 상태 업데이트
                    update_query = (db_models.Model.update(
                        status=ModelStatus.ERROR.value,
                        error_message=f"모델 실행 중 오류: {response_data.get('message', '알 수 없는 오류가 발생했습니다.')}",
                        api_base=None,
                        updated_at=util.get_now()
                    ).where(db_models.Model.name == model_name))
                    await db_manager.execute_query(update_query)
                    
                    logger.error(f"모델 실행에 실패했습니다. (api_base={api_base}, model_name={model_name}, status_code={status_code}, response_data={response_data})")
                    return
                
                # 상태 업데이트
                update_query = (db_models.Model.update(
                    status=ModelStatus.RUNNING.value,
                    error_message=None,
                    api_base=api_base,
                    updated_at=util.get_now()
                ).where(db_models.Model.name == model_name))
                await db_manager.execute_query(update_query)
                
                logger.info(f"모델이 실행되었습니다. (api_base={api_base}, model_name={model_name})")
            except Exception:
                logger.exception(f"모델 실행 중 오류가 발생했습니다. (name={model_name})")
                
                # 상태 업데이트
                update_query = (db_models.Model.update(
                    status=ModelStatus.ERROR.value,
                    error_message="모델 실행 중 오류: 알 수 없는 오류가 발생했습니다.",
                    updated_at=util.get_now()
                ).where(db_models.Model.name == model_name))
                await db_manager.execute_query(update_query)
        
        background_tasks.add_task(_run_local_model)
        
        return BaseMessageResponse(
            message="모델을 실행합니다."
        )

@router.post("/stop/{model_name:path}", summary="모델 실행 중지",
    description="모델을 실행 중지합니다.",
    responses={
        200: {"description": "모델 실행이 중지되었습니다.", "model": BaseMessageResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "lock_acquired": {
                            "summary": "다른 요청이 처리 중임",
                            "value": {
                                "message": "현재 다른 요청을 처리 중입니다. 잠시 후 다시 시도해주세요.",
                                "target": "lock=acquired"
                            }
                        },
                        "not_running": {
                            "summary": "모델이 실행 중이 아님",
                            "value": {
                                "message": "모델이 실행 중이 아닙니다.",
                                "target": "status={status}"
                            }
                        },
                        "node_not_found": {
                            "summary": "모델이 실행 중인 노드를 찾을 수 없음",
                            "value": {
                                "message": "모델이 실행 중인 노드를 찾을 수 없습니다.",
                                "target": "model_name={model_name}"
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
							"summary": "등록되지 않은 모델",
							"value": {
								"message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
								"target": "model_name={model_name}"
							}
						}
					}
				}
			}
		},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def stop_model(
    background_tasks: BackgroundTasks,
    model_name: str = Path(
        ...,
        description="실행 중지할 모델명입니다.",
        examples=["qwen3-32b"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    lock_name = "model_stop"
    
    if is_locked(lock_name):
        return BadRequestResponse(
            message="현재 다른 요청을 처리 중입니다. 잠시 후 다시 시도해주세요.",
            target="lock=acquired"
        )
    
    async with lock(lock_name):
        # 모델 조회
        query = (db_models.Model.select()
                .where(db_models.Model.name == model_name))
        model: Optional[db_items.Model] = await db_manager.select_item(query)
        if not model:
            return NotFoundResponse(
                message="등록되지 않은 모델이거나 실행중이 아닙니다.",
                target=f"model_name={model_name}"
            )
        
        # 모델 상태 확인
        if model.status != ModelStatus.RUNNING:
            return BadRequestResponse(
                message="모델이 실행 중이 아닙니다.",
                target=f"status={model.status.value}"
            )
        
        # 원격 모델 처리
        if model.provider != ModelProvider.LOCAL:
            update_query = (db_models.Model.update(
                status=ModelStatus.STOPPED.value,
                error_message=None,
                updated_at=util.get_now()
            ).where(db_models.Model.name == model_name))
            await db_manager.execute_query(update_query)
            
            return BaseMessageResponse(
                message="모델 실행이 중지되었습니다."
            )
        
        # 로컬 모델 처리

        # 컨테이너 내장 임베딩 모델은 프로세스 메모리에서 내리기만 하면 된다.
        if model.model_type == ModelType.EMBEDDING:
            await embedder.unload_embedder(model.model_id)

            update_query = (db_models.Model.update(
                status=ModelStatus.STOPPED.value,
                error_message=None,
                api_base=None,
                updated_at=util.get_now()
            ).where(db_models.Model.name == model_name))
            await db_manager.execute_query(update_query)

            return BaseMessageResponse(
                message="모델 실행이 중지되었습니다."
            )

        node_info = None

        # 노드가 실행 중인지 확인
        async with get_redis() as redis:
            node_keys = await redis.keys(f"ai-agent-one:nodes:*")
            
            for node_key in node_keys:
                try:
                    node_info_str = await redis.get(node_key)
                    if not node_info_str:
                        continue
                    
                    node_info_temp: dict = orjson.loads(node_info_str)
                    if node_info_temp.get("api_base") == model.api_base:
                        node_info = node_info_temp
                        break
                except orjson.JSONDecodeError:
                    pass
        
        if not node_info:
            return BadRequestResponse(
                message="모델이 실행 중인 노드를 찾을 수 없습니다.",
                target=f"model_name={model_name}"
            )
        
        api_base: str = node_info.get("api_base")
        
        async def _stop_local_model():
            # 상태 업데이트
            update_query = (db_models.Model.update(
                status=ModelStatus.STOPPING.value,
                error_message=None,
                updated_at=util.get_now()
            ).where(db_models.Model.name == model_name))
            await db_manager.execute_query(update_query)
            
            try:
                node_client = NodeClient(base_url=api_base, token=user_info.token)
                status_code, response_data = await node_client.stop_model(model_name=model_name)
                
                if status_code != 200:
                    # 상태 업데이트
                    update_query = (db_models.Model.update(
                        status=ModelStatus.ERROR.value,
                        error_message=f"모델 실행 중지 중 오류: {response_data.get('message', '알 수 없는 오류가 발생했습니다.')}",
                        api_base=None,
                        updated_at=util.get_now()
                    ).where(db_models.Model.name == model_name))
                    await db_manager.execute_query(update_query)
                    
                    logger.error(f"모델 실행 중지에 실패했습니다. (api_base={api_base}, model_name={model_name}, status_code={status_code}, response_data={response_data})")
                    return
                
                # 상태 업데이트
                update_query = (db_models.Model.update(
                    status=ModelStatus.STOPPED.value,
                    error_message=None,
                    api_base=None,
                    updated_at=util.get_now()
                ).where(db_models.Model.name == model_name))
                await db_manager.execute_query(update_query)
                
                logger.info(f"모델 실행이 중지되었습니다. (api_base={api_base}, model_name={model_name})")
            except Exception:
                logger.exception(f"모델 실행 중지 중 오류가 발생했습니다. (name={model_name})")
                
                # 상태 업데이트
                update_query = (db_models.Model.update(
                    status=ModelStatus.ERROR.value,
                    error_message="모델 실행 중지 중 오류: 알 수 없는 오류가 발생했습니다.",
                    updated_at=util.get_now()
                ).where(db_models.Model.name == model_name))
                await db_manager.execute_query(update_query)
        
        background_tasks.add_task(_stop_local_model)
        
        return BaseMessageResponse(
            message="모델 실행을 중지합니다."
        )

from logging import Logger

from typing import Optional

from peewee import fn, IntegrityError

from fastapi import APIRouter, Depends, Path, Query

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
)
from app.payloads.v1.prompt import AddPromptPayload, UpdatePromptPayload, RenderPromptPayload
from app.responses.base import BaseMessageResponse, BaseMessageIdResponse, BaseListResponse
from app.responses.v1.prompt import PromptListItem, PromptVersionListItem, RenderPromptResponse
from app.responses.exception import (
    BadRequestResponse,
    ForbiddenResponse,
    NotFoundResponse,
    ConflictResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import (
    NotFoundError,
    ConflictError,
)
from app.models.api.common import OrderBy
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.access_scope as access_scope
import app.utils.common as util
import app.utils.prompt_renderer as prompt_renderer


router = APIRouter()


@router.get("/list", summary="프롬프트 목록 조회",
    description=(
        "등록된 프롬프트 목록을 페이징 처리하여 조회합니다.  \n"
        "프롬프트의 기본 정보와 최신 버전을 페이지 단위로 반환합니다."
    ),
    responses={
        200: {"description": "프롬프트 목록을 반환합니다.", "model": BaseListResponse[PromptListItem]},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_prompt_list(
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
        OrderBy.CREATED_AT_DESC,
        description="프롬프트 목록 정렬 방식입니다.",
        examples=list(OrderBy)
    ),
    search: Optional[str] = Query(
        None,
        description="프롬프트명 또는 설명으로 검색합니다.",
        examples=["시스템", "코드"]
    ),
    is_system: bool = Query(
        False,
        description="시스템 전용 프롬프트도 포함하여 조회합니다.",
        examples=[False, True]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 기본 쿼리 생성
    query = db_models.Prompt.select()
    count_query = db_models.Prompt.select(fn.COUNT(db_models.Prompt.id).alias("count"))
    
    # 필터 조건 추가
    conditions = []
    if not is_system:
        conditions.append(db_models.Prompt.is_system == False)
    
    if search is not None:
        conditions.append((
            (db_models.Prompt.name.contains(search)) |
            (db_models.Prompt.description.contains(search))
        ))
    
    scope_expr = access_scope.peewee_access_scope_filter(db_models.Prompt.access_scope, user_info)
    if scope_expr is not None:
        conditions.append(scope_expr)
    
    # 조건이 있으면 WHERE 절 추가
    if conditions:
        combined_condition = conditions[0]
        for condition in conditions[1:]:
            combined_condition = combined_condition & condition
        query = query.where(combined_condition)
        count_query = count_query.where(combined_condition)
    
    # 정렬 적용
    if order_by == OrderBy.CREATED_AT_ASC:
        query = query.order_by(db_models.Prompt.created_at.asc())
    elif order_by == OrderBy.CREATED_AT_DESC:
        query = query.order_by(db_models.Prompt.created_at.desc())
    elif order_by == OrderBy.UPDATED_AT_ASC:
        query = query.order_by(db_models.Prompt.updated_at.asc())
    elif order_by == OrderBy.UPDATED_AT_DESC:
        query = query.order_by(db_models.Prompt.updated_at.desc())

    # 페이징 처리 (count가 0이면 전체 조회)
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)
    
    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0
    
    # 목록 조회
    prompts: list[db_items.Prompt] = await db_manager.select_items(query)
    
    # PromptListItem으로 변환
    prompt_items = [PromptListItem.from_prompt(prompt) for prompt in prompts]

    # 총 페이지 수 계산
    if count > 0:
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    else:
        total_pages = 1
    
    return BaseListResponse[PromptListItem](
        total_pages=total_pages,
        total_count=total_count,
        items=prompt_items
    )

@router.get("/info/{prompt_id}", summary="프롬프트 상세 조회",
    description="특정 프롬프트의 상세 정보를 조회합니다.",
    responses={
        200: {"description": "프롬프트 정보를 반환합니다.", "model": db_items.Prompt},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 프롬프트",
                            "value": {
                                "message": "등록되지 않은 프롬프트입니다.",
                                "target": "prompt_id={prompt_id}"
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
async def get_prompt_info(
    prompt_id: int = Path(
        ...,
        description="조회할 프롬프트 ID입니다.",
        examples=[1, 2, 3]
    ),
    is_system: bool = Query(
        False,
        description="시스템 전용 프롬프트도 조회할 수 있습니다.",
        examples=[False, True]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 프롬프트 조회
    query = (db_models.Prompt.select()
             .where(db_models.Prompt.id == prompt_id))
    prompt: Optional[db_items.Prompt] = await db_manager.select_item(query)
    if not prompt:
        return NotFoundResponse(
            message="등록되지 않은 프롬프트입니다.",
            target=f"prompt_id={prompt_id}"
        )
    
    if not access_scope.can_access_scope(user_info, prompt.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")

    return prompt

@router.get("/{prompt_id}/versions", summary="프롬프트 버전 목록 조회",
    description="특정 프롬프트의 모든 버전 목록을 조회합니다.",
    responses={
        200: {"description": "프롬프트 버전 목록을 반환합니다.", "model": list[PromptVersionListItem]},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 프롬프트",
                            "value": {
                                "message": "등록되지 않은 프롬프트입니다.",
                                "target": "prompt_id={prompt_id}"
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
async def get_prompt_versions(
    prompt_id: int = Path(
        ...,
        description="조회할 프롬프트 ID입니다.",
        examples=[1, 2, 3]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 프롬프트 존재 확인
    prompt_query = (db_models.Prompt.select()
                        .where(db_models.Prompt.id == prompt_id))
    prompt: Optional[db_items.Prompt] = await db_manager.select_item(prompt_query)
    if not prompt:
        return NotFoundResponse(
            message="등록되지 않은 프롬프트입니다.",
            target=f"prompt_id={prompt_id}"
        )
    
    if not access_scope.can_access_scope(user_info, prompt.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    # 버전 목록 조회 (최신 버전부터)
    query = (db_models.PromptVersion.select()
                .where(db_models.PromptVersion.prompt_id == prompt_id)
                .order_by(db_models.PromptVersion.version.desc()))
    
    versions: list[db_items.PromptVersion] = await db_manager.select_items(query)
    
    # PromptVersionListItem으로 변환
    version_items = [PromptVersionListItem.from_version(version) for version in versions]
    
    return version_items

@router.get("/{prompt_id}/versions/{version}", summary="특정 버전 프롬프트 조회",
    description="특정 프롬프트의 특정 버전을 조회합니다.",
    responses={
        200: {"description": "프롬프트 버전 정보를 반환합니다.", "model": db_items.PromptVersion},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found_prompt": {
                            "summary": "등록되지 않은 프롬프트",
                            "value": {
                                "message": "등록되지 않은 프롬프트입니다.",
                                "target": "prompt_id={prompt_id}"
                            }
                        },
                        "not_found_version": {
                            "summary": "존재하지 않는 버전",
                            "value": {
                                "message": "존재하지 않는 버전입니다.",
                                "target": "prompt_id={prompt_id}, version={version}"
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
async def get_prompt_version(
    prompt_id: int = Path(
        ...,
        description="조회할 프롬프트 ID입니다.",
        examples=[1, 2, 3]
    ),
    version: int = Path(
        ..., ge=1,
        description="조회할 버전 번호입니다.",
        examples=[1, 2, 3]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 프롬프트 존재 확인
    prompt_query = (db_models.Prompt.select()
                        .where(db_models.Prompt.id == prompt_id))
    prompt: Optional[db_items.Prompt] = await db_manager.select_item(prompt_query)
    if not prompt:
        return NotFoundResponse(
            message="등록되지 않은 프롬프트입니다.",
            target=f"prompt_id={prompt_id}"
        )
    
    if not access_scope.can_access_scope(user_info, prompt.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    # 버전 조회
    query = (db_models.PromptVersion.select()
                .where(
                    (db_models.PromptVersion.prompt_id == prompt_id) &
                    (db_models.PromptVersion.version == version)
                ))
    prompt_version: Optional[db_items.PromptVersion] = await db_manager.select_item(query)
    if not prompt_version:
        return NotFoundResponse(
            message="존재하지 않는 버전입니다.",
            target=f"prompt_id={prompt_id}, version={version}"
        )

    return prompt_version

@router.post("/add", summary="프롬프트 추가",
    description="새로운 프롬프트를 추가합니다.",
    responses={
        200: {"description": "프롬프트가 추가되었습니다.", "model": BaseMessageIdResponse[int]},
        409: {
            "description": "이미 등록된 항목입니다.", 
            "model": ConflictError,
            "content": {
                "application/json": {
                    "examples": {
                        "already_added": {
                            "summary": "이미 등록된 프롬프트",
                            "value": {
                                "message": "이미 등록된 프롬프트입니다.",
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
async def add_prompt(
    payload: AddPromptPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    try:
        scope_value = access_scope.validate_access_scope(payload.access_scope)
    except ValueError as exc:
        return BadRequestResponse(message=str(exc), target=f"access_scope={payload.access_scope}")
    try:
        # 프롬프트 생성
        query = db_models.Prompt.insert(
            name=payload.name,
            description=payload.description,
            content=payload.content,
            current_version=1,
            tags=payload.tags,
            access_scope=scope_value,
            created_by=user_info.username
        )
        result = await db_manager.execute_query(query)
        prompt_id = result
    except IntegrityError:
        return ConflictResponse(
            message="이미 등록된 프롬프트입니다.",
            target=f"name={payload.name}"
        )
    
    logger.info(f"프롬프트가 추가되었습니다. (prompt_id={prompt_id}, name={payload.name})")
    
    return BaseMessageIdResponse[int](
        message="프롬프트가 추가되었습니다.",
        id=prompt_id
    )

@router.patch("/update/{prompt_id}", summary="프롬프트 수정",
    description=(
        "프롬프트 정보를 수정합니다.  \n"
        "프롬프트 내용이 변경되면 이전 내용은 백업됩니다."
	),
    responses={
        200: {"description": "프롬프트 정보가 수정되었습니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 프롬프트",
                            "value": {
                                "message": "등록되지 않은 프롬프트입니다.",
                                "target": "prompt_id={prompt_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_prompt(
    payload: UpdatePromptPayload,
    prompt_id: int = Path(
        ...,
        description="수정할 프롬프트 ID입니다.",
        examples=[1, 2, 3]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 기존 프롬프트 조회
    query = (db_models.Prompt.select()
                .where(db_models.Prompt.id == prompt_id))
    existing_prompt: Optional[db_items.Prompt] = await db_manager.select_item(query)
    if not existing_prompt:
        return NotFoundResponse(
            message="등록되지 않은 프롬프트입니다.",
            target=f"prompt_id={prompt_id}"
        )
    
    # 업데이트할 값 확인
    update_values = payload.model_dump(exclude_unset=True)
    if "access_scope" in update_values:
        try:
            update_values["access_scope"] = access_scope.validate_access_scope(update_values["access_scope"])
        except ValueError as exc:
            return BadRequestResponse(message=str(exc), target=f"access_scope={update_values['access_scope']}")
    
    # content가 변경되면 새 버전 생성
    if "content" in update_values and update_values["content"] != existing_prompt.content:
        new_version = existing_prompt.current_version + 1
        
        # 기존 프롬프트 내용을 새 버전으로 백업 (이전 버전 보존)
        version_query = db_models.PromptVersion.insert(
            prompt_id=prompt_id,
            version=existing_prompt.current_version,
            content=existing_prompt.content,
            change_note=update_values.get("change_note", f"Ver {existing_prompt.current_version}"),
            created_by=user_info.username
        )
        await db_manager.execute_query(version_query)
        
        # current_version 업데이트
        update_values["current_version"] = new_version
    
    # change_note는 content 변경 시에만 사용되므로 제거
    update_values.pop("change_note", None)
    
    if update_values:
        update_values["updated_at"] = util.get_now()
        
        # 프롬프트 정보 수정
        update_query = (db_models.Prompt.update(**update_values)
                    .where(db_models.Prompt.id == prompt_id))
        await db_manager.execute_query(update_query)
    
    logger.info(f"프롬프트 ID {prompt_id} 정보가 수정되었습니다.")
    
    return BaseMessageResponse(message="프롬프트 정보가 수정되었습니다.")

@router.delete("/delete/{prompt_id}", summary="프롬프트 삭제",
    description=(
        "프롬프트와 모든 버전을 삭제합니다.  \n"
        "버전 내역도 함께 삭제됩니다."
	),
    responses={
        200: {"description": "프롬프트가 삭제되었습니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 프롬프트",
                            "value": {
                                "message": "등록되지 않은 프롬프트입니다.",
                                "target": "prompt_id={prompt_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_prompt(
    prompt_id: int = Path(
        ...,
        description="삭제할 프롬프트 ID입니다.",
        examples=[1, 2, 3]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 프롬프트 존재 확인
    query = (db_models.Prompt.select()
                .where(db_models.Prompt.id == prompt_id))
    prompt: Optional[db_items.Prompt] = await db_manager.select_item(query)
    if not prompt:
        return NotFoundResponse(
            message="등록되지 않은 프롬프트입니다.",
            target=f"prompt_id={prompt_id}"
        )
    
    # 버전 삭제
    version_delete_query = (db_models.PromptVersion.delete()
                                .where(db_models.PromptVersion.prompt_id == prompt_id))
    await db_manager.execute_query(version_delete_query)
    
    # 프롬프트 삭제
    delete_query = (db_models.Prompt.delete()
                        .where(db_models.Prompt.id == prompt_id))
    await db_manager.execute_query(delete_query)

    logger.info(f"프롬프트가 삭제되었습니다. (prompt_id={prompt_id})")
    return BaseMessageResponse(message="프롬프트가 삭제되었습니다.")

@router.post("/render/{prompt_id}", summary="프롬프트 렌더링",
    description=(
        "프롬프트의 변수를 렌더링하여 최종 프롬프트를 반환합니다.  \n"
        "시스템 변수(날짜/시간, 사용자 정보)와 커스텀 변수를 렌더링합니다."
    ),
    responses={
        200: {"description": "렌더링된 프롬프트를 반환합니다.", "model": RenderPromptResponse},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 프롬프트",
                            "value": {
                                "message": "등록되지 않은 프롬프트입니다.",
                                "target": "prompt_id={prompt_id}"
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
async def render_prompt(
    payload: RenderPromptPayload,
    prompt_id: int = Path(
        ...,
        description="렌더링할 프롬프트 ID입니다.",
        examples=[1, 2, 3]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 프롬프트 조회
    query = (db_models.Prompt.select()
                .where(db_models.Prompt.id == prompt_id))
    prompt: Optional[db_items.Prompt] = await db_manager.select_item(query)
    if not prompt:
        return NotFoundResponse(
            message="등록되지 않은 프롬프트입니다.",
            target=f"prompt_id={prompt_id}"
        )
    
    if not access_scope.can_access_scope(user_info, prompt.access_scope):
        return ForbiddenResponse(message="접근 권한이 없습니다.")
    
    # 렌더링
    rendered_content = prompt_renderer.render(
        content=prompt.content,
        user_info=user_info,
        variables=payload.variables
    )
    
    return RenderPromptResponse(
        rendered_content=rendered_content
    )

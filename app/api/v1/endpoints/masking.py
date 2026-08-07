from logging import Logger

from typing import Optional
from uuid import UUID, uuid4

from peewee import fn

from fastapi import APIRouter, Depends, Path, Query

from app.config import config
from app.exceptions.api_exception import DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN
from app.payloads.v1.masking import (
    CreateMaskingRulePayload,
    UpdateMaskingRulePayload,
    TestMaskingPayload,
)
from app.responses.base import BaseListResponse, BaseMessageResponse
from app.responses.v1.masking import MaskingRuleInfo, TestMaskingResponse
from app.responses.exception import (
    BadRequestResponse,
    NotFoundResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import BadRequestError, NotFoundError
from app.models.enum import MaskingTargetField, MaskingMethod
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.pii_mask import apply_masking, load_active_rules, validate_regex
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


def _to_info(rule: db_items.MaskingRule) -> MaskingRuleInfo:
    return MaskingRuleInfo(
        id=rule.id,
        name=rule.name,
        target_field=rule.target_field,
        regex_pattern=rule.regex_pattern,
        masking_method=rule.masking_method,
        replacement=rule.replacement,
        description=rule.description,
        is_active=rule.is_active,
        created_at=util.serialize_datetime(rule.created_at),
        updated_at=util.serialize_datetime(rule.updated_at),
    )


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


@router.get("/items", summary="마스킹 규칙 목록 조회",
    description="개인정보 마스킹 규칙 목록을 검색·필터·페이징하여 조회합니다.",
    responses={
        200: {"description": "규칙 목록을 반환합니다.", "model": BaseListResponse[MaskingRuleInfo]},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def list_masking_rules(
    page: int = Query(1, ge=1, description="페이지 번호입니다."),
    count: int = Query(10, ge=0, le=100, description="페이지당 항목 수입니다. (0이면 전체)"),
    search: Optional[str] = Query(None, description="규칙명·대상 필드·설명 검색어입니다."),
    is_active: Optional[bool] = Query(None, description="활성 여부로 필터링합니다."),
    masking_method: Optional[MaskingMethod] = Query(None, description="마스킹 방식으로 필터링합니다."),
    target_field: Optional[MaskingTargetField] = Query(None, description="대상 필드로 필터링합니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    query = db_models.MaskingRule.select()
    count_query = db_models.MaskingRule.select(fn.COUNT(db_models.MaskingRule.id).alias("count"))

    conditions = []
    if is_active is not None:
        conditions.append(db_models.MaskingRule.is_active == is_active)
    if masking_method is not None:
        conditions.append(db_models.MaskingRule.masking_method == masking_method.value)
    if target_field is not None:
        conditions.append(db_models.MaskingRule.target_field == target_field.value)
    if search:
        conditions.append(
            (db_models.MaskingRule.name.contains(search)) |
            (db_models.MaskingRule.description.contains(search)) |
            (db_models.MaskingRule.target_field.contains(search)) |
            (db_models.MaskingRule.regex_pattern.contains(search))
        )

    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)

    query = query.order_by(db_models.MaskingRule.updated_at.desc())

    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    else:
        total_pages = 1

    rules: list[db_items.MaskingRule] = await db_manager.select_items(query)
    logger.debug(f"마스킹 규칙 목록 조회 (total={total_count})")
    return BaseListResponse[MaskingRuleInfo](
        total_pages=total_pages,
        total_count=total_count,
        items=[_to_info(rule) for rule in rules],
    )


@router.get("/items/{rule_id}", summary="마스킹 규칙 상세 조회",
    responses={
        200: {"description": "규칙 상세를 반환합니다.", "model": MaskingRuleInfo},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_masking_rule(
    rule_id: UUID = Path(..., description="규칙 ID입니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
):
    query = db_models.MaskingRule.select().where(db_models.MaskingRule.id == rule_id)
    rule: Optional[db_items.MaskingRule] = await db_manager.select_item(query)
    if not rule:
        return NotFoundResponse(message="마스킹 규칙을 찾을 수 없습니다.", target=f"rule_id={rule_id}")
    return _to_info(rule)


@router.post("/items", summary="마스킹 규칙 생성",
    responses={
        200: {"description": "생성된 규칙을 반환합니다.", "model": MaskingRuleInfo},
        400: {"description": "잘못된 요청입니다.", "model": BadRequestError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def create_masking_rule(
    payload: CreateMaskingRulePayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    try:
        validate_regex(payload.regex_pattern)
    except ValueError as exc:
        return BadRequestResponse(message=str(exc), target="regex_pattern")

    rule_id = uuid4()
    now = util.get_now()
    await db_manager.execute_query(
        db_models.MaskingRule.insert(
            id=rule_id,
            name=payload.name,
            target_field=_enum_value(payload.target_field),
            regex_pattern=payload.regex_pattern,
            masking_method=_enum_value(payload.masking_method),
            replacement=payload.replacement,
            description=payload.description,
            is_active=payload.is_active,
            created_at=now,
            updated_at=now,
        )
    )
    logger.info(f"마스킹 규칙이 생성되었습니다. (id={rule_id}, name={payload.name})")
    query = db_models.MaskingRule.select().where(db_models.MaskingRule.id == rule_id)
    rule = await db_manager.select_item(query)
    return _to_info(rule)


@router.patch("/items/{rule_id}", summary="마스킹 규칙 수정",
    responses={
        200: {"description": "수정된 규칙을 반환합니다.", "model": MaskingRuleInfo},
        400: {"description": "잘못된 요청입니다.", "model": BadRequestError},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_masking_rule(
    payload: UpdateMaskingRulePayload,
    rule_id: UUID = Path(..., description="규칙 ID입니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    query = db_models.MaskingRule.select().where(db_models.MaskingRule.id == rule_id)
    rule: Optional[db_items.MaskingRule] = await db_manager.select_item(query)
    if not rule:
        return NotFoundResponse(message="마스킹 규칙을 찾을 수 없습니다.", target=f"rule_id={rule_id}")

    payload_set = payload.model_dump(exclude_unset=True)
    if "regex_pattern" in payload_set and payload_set["regex_pattern"] is not None:
        try:
            validate_regex(payload_set["regex_pattern"])
        except ValueError as exc:
            return BadRequestResponse(message=str(exc), target="regex_pattern")

    update_data = {}
    for key in ("name", "regex_pattern", "replacement", "description", "is_active"):
        if key in payload_set:
            update_data[key] = payload_set[key]
    if "target_field" in payload_set and payload_set["target_field"] is not None:
        update_data["target_field"] = _enum_value(payload_set["target_field"])
    if "masking_method" in payload_set and payload_set["masking_method"] is not None:
        update_data["masking_method"] = _enum_value(payload_set["masking_method"])

    if update_data:
        update_data["updated_at"] = util.get_now()
        await db_manager.execute_query(
            db_models.MaskingRule.update(**update_data).where(db_models.MaskingRule.id == rule_id)
        )
        logger.info(f"마스킹 규칙이 수정되었습니다. (id={rule_id})")
        rule = await db_manager.select_item(query)

    return _to_info(rule)


@router.delete("/items/{rule_id}", summary="마스킹 규칙 삭제",
    responses={
        200: {"description": "삭제 결과를 반환합니다.", "model": BaseMessageResponse},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_masking_rule(
    rule_id: UUID = Path(..., description="규칙 ID입니다."),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    query = db_models.MaskingRule.select().where(db_models.MaskingRule.id == rule_id)
    rule: Optional[db_items.MaskingRule] = await db_manager.select_item(query)
    if not rule:
        return NotFoundResponse(message="마스킹 규칙을 찾을 수 없습니다.", target=f"rule_id={rule_id}")

    await db_manager.execute_query(
        db_models.MaskingRule.delete().where(db_models.MaskingRule.id == rule_id)
    )
    logger.info(f"마스킹 규칙이 삭제되었습니다. (id={rule_id})")
    return BaseMessageResponse(message="마스킹 규칙이 삭제되었습니다.")


@router.post("/test", summary="마스킹 미리보기",
    description="샘플 텍스트에 활성 규칙(또는 지정 규칙)을 적용한 결과를 반환합니다.",
    responses={
        200: {"description": "미리보기 결과를 반환합니다.", "model": TestMaskingResponse},
        404: {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def test_masking(
    payload: TestMaskingPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
):
    if payload.rule_id:
        query = db_models.MaskingRule.select().where(db_models.MaskingRule.id == payload.rule_id)
        rule: Optional[db_items.MaskingRule] = await db_manager.select_item(query)
        if not rule:
            return NotFoundResponse(
                message="마스킹 규칙을 찾을 수 없습니다.",
                target=f"rule_id={payload.rule_id}"
            )
        rules = [rule]
    else:
        rules = await load_active_rules(db_manager)

    masked = apply_masking(payload.text, rules)
    return TestMaskingResponse(
        original=payload.text,
        masked=masked,
        applied_rule_count=len(rules),
    )

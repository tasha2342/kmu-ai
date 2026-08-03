from pydantic import BaseModel, Field

from typing import Optional
from uuid import UUID

from app.models.enum import MaskingTargetField, MaskingMethod


class CreateMaskingRulePayload(BaseModel):
    """마스킹 규칙 생성 요청"""

    name: str = Field(..., min_length=1, max_length=255, description="규칙명입니다.", examples=["전화번호"])
    target_field: MaskingTargetField = Field(
        ...,
        description="대상 필드 분류입니다.",
        examples=list(MaskingTargetField)
    )
    regex_pattern: str = Field(
        ...,
        min_length=1,
        description="정규표현식입니다.",
        examples=[r"01[0-9]-?\d{3,4}-?\d{4}"]
    )
    masking_method: MaskingMethod = Field(
        ...,
        description="마스킹 방식입니다.",
        examples=list(MaskingMethod)
    )
    replacement: str = Field(
        "****",
        max_length=64,
        description="치환 문자열입니다.",
        examples=["****"]
    )
    description: Optional[str] = Field(None, description="설명입니다.")
    is_active: bool = Field(True, description="활성 여부입니다.")


class UpdateMaskingRulePayload(BaseModel):
    """마스킹 규칙 수정 요청"""

    name: Optional[str] = Field(None, min_length=1, max_length=255, description="규칙명입니다.")
    target_field: Optional[MaskingTargetField] = Field(None, description="대상 필드 분류입니다.")
    regex_pattern: Optional[str] = Field(None, min_length=1, description="정규표현식입니다.")
    masking_method: Optional[MaskingMethod] = Field(None, description="마스킹 방식입니다.")
    replacement: Optional[str] = Field(None, max_length=64, description="치환 문자열입니다.")
    description: Optional[str] = Field(None, description="설명입니다.")
    is_active: Optional[bool] = Field(None, description="활성 여부입니다.")


class TestMaskingPayload(BaseModel):
    """마스킹 미리보기 요청"""

    text: str = Field(..., min_length=1, description="테스트할 원문입니다.")
    rule_id: Optional[UUID] = Field(
        None,
        description="특정 규칙만 적용합니다. 생략 시 활성 규칙 전부 적용합니다."
    )

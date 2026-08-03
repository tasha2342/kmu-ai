from pydantic import BaseModel, Field

from typing import Optional
from uuid import UUID

from app.models.enum import MaskingTargetField, MaskingMethod


class MaskingRuleInfo(BaseModel):
    """마스킹 규칙 정보"""

    id: UUID = Field(..., description="규칙 ID입니다.")
    name: str = Field(..., description="규칙명입니다.", examples=["전화번호"])
    target_field: MaskingTargetField = Field(
        ...,
        description="대상 필드 분류입니다.",
        examples=list(MaskingTargetField)
    )
    regex_pattern: str = Field(..., description="정규표현식입니다.")
    masking_method: MaskingMethod = Field(
        ...,
        description="마스킹 방식입니다.",
        examples=list(MaskingMethod)
    )
    replacement: str = Field(..., description="치환 문자열입니다.", examples=["****"])
    description: Optional[str] = Field(None, description="설명입니다.")
    is_active: bool = Field(..., description="활성 여부입니다.")
    created_at: str = Field(..., description="생성 일시입니다.")
    updated_at: str = Field(..., description="수정 일시입니다.")


class TestMaskingResponse(BaseModel):
    """마스킹 미리보기 응답"""

    original: str = Field(..., description="원문입니다.")
    masked: str = Field(..., description="마스킹된 결과입니다.")
    applied_rule_count: int = Field(..., description="적용된 규칙 수입니다.")

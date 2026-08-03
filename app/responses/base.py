from pydantic import BaseModel, Field

from typing import TypeVar, Generic


T = TypeVar("T")


class BaseMessageResponse(BaseModel):
    """기본 응답"""
    
    message: str = Field(
        ...,
        description="응답 메시지입니다.",
        examples=["`{응답 메시지}`"]
    )

class BaseMessageIdResponse(BaseMessageResponse, Generic[T]):
    """기본 응답 (ID 포함)"""
    
    id: T = Field(
        ...,
        description="항목의 ID입니다.",
        examples=[]
    )
    
    
    def __init__(self, **data):
        super().__init__(**data)
        if isinstance(self.id, int):
            self.model_fields["id"].examples = [1]
        elif isinstance(self.id, str):
            self.model_fields["id"].examples = ["JD001"]

class BaseListResponse(BaseModel, Generic[T]):
    """목록 응답 (페이징 정보 포함)"""
    
    total_pages: int = Field(
        ...,
        description="총 페이지 수입니다.",
        examples=[2]
    )
    total_count: int = Field(
        ...,
        description="총 항목 수입니다.",
        examples=[16]
    )
    items: list[T] = Field(
        ...,
        description="목록입니다."
    )

class BaseListResponseWithOriginal(BaseListResponse[T], Generic[T]):
    """목록 응답 (원본 총 항목 수 포함)"""
    
    ori_total_count: int = Field(
        ...,
        description="원본 데이터의 총 항목 수입니다.",
        examples=[100]
    )

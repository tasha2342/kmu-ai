from pydantic import BaseModel, Field

from enum import Enum

from typing import Optional


class OrderBy(str, Enum):
    """정렬 방식"""
    
    CREATED_AT_ASC = "created_at_asc"
    """생성일 오름차순"""
    CREATED_AT_DESC = "created_at_desc"
    """생성일 내림차순"""
    UPDATED_AT_ASC = "updated_at_asc"
    """수정일 오름차순"""
    UPDATED_AT_DESC = "updated_at_desc"
    """수정일 내림차순"""
    
    
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "created_at_asc": "생성일 오름차순",
                "created_at_desc": "생성일 내림차순",
                "updated_at_asc": "수정일 오름차순",
                "updated_at_desc": "수정일 내림차순",
            }
        })
        return json_schema

class OrderByWithId(str, Enum):
    """정렬 방식 (ID 포함)"""
    
    ID_ASC = "id_asc"
    """ID 오름차순"""
    ID_DESC = "id_desc"
    """ID 내림차순"""
    CREATED_AT_ASC = "created_at_asc"
    """생성일 오름차순"""
    CREATED_AT_DESC = "created_at_desc"
    """생성일 내림차순"""
    UPDATED_AT_ASC = "updated_at_asc"
    """수정일 오름차순"""
    UPDATED_AT_DESC = "updated_at_desc"
    """수정일 내림차순"""
    
    
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "id_asc": "ID 오름차순",
                "id_desc": "ID 내림차순",
                "created_at_asc": "생성일 오름차순",
                "created_at_desc": "생성일 내림차순",
                "updated_at_asc": "수정일 오름차순",
                "updated_at_desc": "수정일 내림차순",
            }
        })
        return json_schema


class UserModel(BaseModel):
    """사용자"""
    
    sub: str = Field(
        ...,
        description="사용자 식별 ID입니다.",
        examples=["c41dace4-b68a-4015-9595-920d597a7781"]
    )
    username: str = Field(
        ...,
        description="사용자명입니다.",
        examples=["admin"]
    )
    last_name: Optional[str] = Field(
        None,
        description="사용자 성입니다.",
        examples=["홍"]
    )
    first_name: Optional[str] = Field(
        None,
        description="사용자 이름입니다.",
        examples=["길동"]
    )
    email: Optional[str] = Field(
        None,
        description="사용자 이메일입니다.",
        examples=["admin@example.com"]
    )

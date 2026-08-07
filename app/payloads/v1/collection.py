from pydantic import BaseModel, Field

from typing import Optional


class CreateCollectionPayload(BaseModel):
    """컬렉션 생성 요청 데이터"""
    
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    embedding_model: str = Field(
        ...,
        description=(
            "임베딩에 사용할 모델명입니다.  \n"
            "모델 목록에 등록된 활성화된 임베딩 모델이어야 합니다."
        ),
        examples=["text-embedding-3-small"]
    )
    description: Optional[str] = Field(
        None, max_length=1024,
        description="컬렉션 설명입니다."
    )
    access_scope: Optional[str] = Field(
        None,
        description=(
            "접근 가능 범위입니다.  \n"
            "- **NULL**: 모두 접근 가능  \n"
            "- **group:{group_name}**: 해당 그룹에 속한 사용자만 접근 가능  \n"
            "- **user:{user_name}**: 해당 사용자만 접근 가능  \n"
        ),
        examples=[None, "group:admin", "user:admin"]
    )
    is_system: bool = Field(
        False,
        description=(
            "시스템 전용 컬렉션 여부입니다.  \n"
            "목록/상세 조회 시 기본적으로 제외됩니다."
        ),
        examples=[False, True]
    )


class UpdateCollectionPayload(BaseModel):
    """컬렉션 수정 요청 데이터"""
    
    description: Optional[str] = Field(
        None,
        max_length=1024,
        description="컬렉션 설명입니다."
    )
    access_scope: Optional[str] = Field(
        None,
        description=(
            "접근 가능 범위입니다.  \n"
            "- **NULL**: 모두 접근 가능  \n"
            "- **group:{group_name}**: 해당 그룹에 속한 사용자만 접근 가능  \n"
            "- **user:{user_name}**: 해당 사용자만 접근 가능  \n"
        ),
        examples=[None, "group:admin", "user:admin"]
    )
    is_system: Optional[bool] = Field(
        None,
        description=(
            "시스템 전용 컬렉션 여부입니다.  \n"
            "요청에 포함된 경우에만 변경됩니다."
        ),
        examples=[False, True]
    )
    is_active: Optional[bool] = Field(
        None,
        description=(
            "검색(RAG) 활성 여부입니다.  \n"
            "요청에 포함된 경우에만 변경됩니다."
        ),
        examples=[True, False]
    )

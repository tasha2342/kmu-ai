from pydantic import BaseModel, Field

from typing import Optional

from app.models.enum import GraphIndexStatus


class CollectionInfo(BaseModel):
    """컬렉션 정보"""
    
    name: str = Field(
        ...,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    user_name: str = Field(
        ...,
        description="생성자의 사용자명입니다."
    )
    embedding_model: str = Field(
        ...,
        description="임베딩에 사용된 모델명입니다.",
        examples=["text-embedding-3-small"]
    )
    vector_size: int = Field(
        ...,
        description="벡터 차원 크기입니다.",
        examples=[1536]
    )
    description: Optional[str] = Field(
        None,
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
    is_active: bool = Field(
        True,
        description="검색(RAG) 활성 여부입니다.",
        examples=[True, False]
    )
    document_count: int = Field(
        0,
        description="문서 개수입니다.",
        examples=[10]
    )
    chunk_count: int = Field(
        0,
        description="청크 개수입니다.",
        examples=[250]
    )
    graph_index_status: GraphIndexStatus = Field(
        GraphIndexStatus.NO_INDEXED,
        description="그래프 인덱싱 상태입니다.",
        examples=list(GraphIndexStatus)
    )
    graph_index_error: Optional[str] = Field(
        None,
        description="그래프 인덱싱 오류 메시지입니다."
    )
    graph_indexed_at: Optional[str] = Field(
        None,
        description="마지막 그래프 인덱싱 날짜입니다."
    )
    created_at: str = Field(
        ...,
        description="생성 날짜입니다."
    )
    updated_at: str = Field(
        ...,
        description="수정 날짜입니다."
    )

class CollectionsListResponse(BaseModel):
    """컬렉션 목록 응답"""
    
    collections: list[CollectionInfo] = Field(
        ...,
        description="컬렉션 리스트입니다."
    )
    total_count: int = Field(
        ...,
        description="총 컬렉션 수입니다.",
        examples=[3]
    )

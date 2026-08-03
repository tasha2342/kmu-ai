from pydantic import BaseModel, Field

from typing import Any, Optional


class DocumentPageContent(BaseModel):
    """문서 페이지 내용"""
    
    page: int = Field(
        ..., ge=1,
        description="페이지 번호입니다.",
        examples=[1]
    )
    content: str = Field(
        ..., min_length=1,
        description="페이지 텍스트 내용입니다.",
        examples=["이 문서는 프로젝트 개요를 설명합니다."]
    )


class DocumentFileData(BaseModel):
    """사전 파싱된 문서 데이터"""
    
    contents: list[DocumentPageContent] = Field(
        ...,
        description="페이지별 텍스트 내용 목록입니다."
    )
    metadata: dict[str, Any] = Field(
        ...,
        description="문서 메타데이터입니다.",
        examples=[{"author": "홍길동", "page_count": 5, "title": "프로젝트 문서"}]
    )


class SearchDocumentPayload(BaseModel):
    """문서 검색 요청 데이터"""
    
    collection_name: str = Field(
        ...,
        description="검색할 컬렉션 이름입니다.",
        examples=["test"]
    )
    query: str = Field(
        ...,
        description="검색 쿼리입니다.",
        examples=["프로젝트의 주요 목표는 무엇인가요?"]
    )
    top_k: Optional[int] = Field(
        5,
        ge=1,
        le=50,
        description="반환할 결과 개수입니다. (기본값: 5)",
        examples=[5, 10]
    )
    score_threshold: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="최소 유사도 점수입니다. (0.0 ~ 1.0)",
        examples=[0.7, 0.8]
    )

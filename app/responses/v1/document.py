from pydantic import BaseModel, Field

from typing import Optional, Any

from app.models.enum import GraphIndexStatus
import app.models.db_item as db_items


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


class DocumentInfo(db_items.Document):
    """문서 정보 응답 모델"""
    
    file_url: str = Field(
        ...,
        description="문서 파일 다운로드 URL입니다."
    )


class UploadDocumentResponse(BaseModel):
    """문서 업로드 응답 데이터"""
    
    document_id: int = Field(
        ...,
        description="문서 ID입니다.",
        examples=[1]
    )
    file_name: str = Field(
        ...,
        description="파일명입니다.",
        examples=["document.pdf"]
    )
    collection_name: str = Field(
        ...,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    chunk_count: int = Field(
        ...,
        description=(
            "생성된 청크 개수입니다.  \n"
            "백그라운드에서 처리되는 경우에는 0일 수 있습니다."
		),
        examples=[25]
    )


class SearchResult(BaseModel):
    """검색 결과 아이템"""
    
    document_id: int = Field(
        ...,
        description="문서 ID입니다.",
        examples=[1]
    )
    file_name: str = Field(
        ...,
        description="파일명입니다.",
        examples=["document.pdf"]
    )
    chunk_index: int = Field(
        ...,
        description="청크 인덱스입니다.",
        examples=[0]
    )
    content: str = Field(
        ...,
        description="청크 내용입니다.",
        examples=["프로젝트의 주요 목표는..."]
    )
    page: Optional[int] = Field(
        None,
        description="해당 내용이 위치한 문서 페이지 번호입니다.",
        examples=[1]
    )
    score: float = Field(
        ...,
        description="유사도 점수입니다. (0.0 ~ 1.0)",
        examples=[0.85]
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="문서 메타데이터입니다.",
        examples=[{
            "title": "프로젝트 문서",
            "author": "홍길동",
            "page_count": 10
        }]
    )

class SearchDocumentResponse(BaseModel):
    """문서 검색 응답"""
    
    query: str = Field(
        ...,
        description="검색 쿼리입니다.",
        examples=["프로젝트의 주요 목표는 무엇인가요?"]
    )
    collection_name: str = Field(
        ...,
        description="검색한 컬렉션 이름입니다.",
        examples=["test"]
    )
    results: list[SearchResult] = Field(
        ...,
        description="검색 결과 리스트입니다."
    )
    total_results: int = Field(
        ...,
        description="검색된 총 결과 개수입니다.",
        examples=[5]
    )


class ParsedPageContent(BaseModel):
    """파싱된 페이지 콘텐츠"""

    page: int = Field(
        ...,
        description="페이지 번호입니다.",
        examples=[1]
    )
    content: str = Field(
        ...,
        description="페이지 텍스트 내용입니다.",
        examples=["이 문서는 프로젝트 개요를 설명합니다."]
    )


class DocumentChunk(BaseModel):
    """문서 청크 항목"""

    chunk_index: int = Field(
        ...,
        description="청크 인덱스입니다.",
        examples=[0]
    )
    content: str = Field(
        ...,
        description="청크 텍스트 내용입니다.",
        examples=["프로젝트의 주요 목표는..."]
    )
    page: Optional[int] = Field(
        None,
        description="해당 청크가 속한 문서 페이지 번호입니다.",
        examples=[1]
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="청크 메타데이터입니다."
    )

class DocumentContentResponse(BaseModel):
    """문서 전체 내용 응답"""

    document_id: int = Field(
        ...,
        description="문서 ID입니다.",
        examples=[1]
    )
    file_name: str = Field(
        ...,
        description="파일명입니다.",
        examples=["document.pdf"]
    )
    collection_name: str = Field(
        ...,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    total_chunks: int = Field(
        ...,
        description="총 청크 수입니다.",
        examples=[25]
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="문서 메타데이터입니다.",
        examples=[{"title": "프로젝트 문서", "author": "홍길동", "page_count": 5}]
    )
    chunks: list[DocumentChunk] = Field(
        ...,
        description="청크 인덱스 순서로 정렬된 문서 내용 목록입니다."
    )

class ParseDocumentResponse(BaseModel):
    """문서 파싱 응답"""

    file_name: str = Field(
        ...,
        description="파일명입니다.",
        examples=["document.pdf"]
    )
    total_pages: int = Field(
        ...,
        description="총 페이지 수입니다.",
        examples=[5]
    )
    contents: list[ParsedPageContent] = Field(
        ...,
        description="페이지별 파싱된 콘텐츠 목록입니다."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="문서 메타데이터입니다.",
        examples=[{"title": "프로젝트 문서", "author": "홍길동", "page_count": 5}]
    )

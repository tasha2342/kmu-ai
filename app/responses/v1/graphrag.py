from pydantic import BaseModel, Field

from typing import Optional

from app.models.enum import GraphIndexStatus


class GraphStatsResponse(BaseModel):
    """GraphRAG 통계 응답"""
    
    collection_name: str = Field(
        ...,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    entity_count: int = Field(
        ...,
        description="총 엔티티 수입니다.",
        examples=[150]
    )
    relationship_count: int = Field(
        ...,
        description="총 관계 수입니다.",
        examples=[80]
    )
    community_count: int = Field(
        ...,
        description="총 커뮤니티 수입니다.",
        examples=[5]
    )


class GraphIndexResponse(BaseModel):
    """GraphRAG 인덱싱 응답"""
    
    collection_name: str = Field(
        ...,
        description="인덱싱된 컬렉션 이름입니다.",
        examples=["test"]
    )
    status: GraphIndexStatus = Field(
        ...,
        description="인덱싱 상태입니다.",
        examples=list(GraphIndexStatus)
    )
    document_count: int = Field(
        0,
        description="처리된 문서 수입니다.",
        examples=[10]
    )
    entity_count: int = Field(
        0,
        description="추출된 엔티티 수입니다.",
        examples=[150]
    )
    relationship_count: int = Field(
        0,
        description="추출된 관계 수입니다.",
        examples=[80]
    )
    community_count: int = Field(
        0,
        description="탐지된 커뮤니티 수입니다.",
        examples=[5]
    )
    message: str = Field(
        ...,
        description="처리 결과 메시지입니다."
    )


class FacilitySyncResponse(BaseModel):
    """시설 데이터 동기화 응답"""

    collection_name: str = Field(
        ...,
        description="적재된 컬렉션 이름입니다.",
        examples=["pet-pass-one-facilities"]
    )
    synced_count: int = Field(
        ...,
        description="동기화(upsert)된 시설 수입니다.",
        examples=[42]
    )
    message: str = Field(
        ...,
        description="처리 결과 메시지입니다."
    )


class EntityInfo(BaseModel):
    """엔티티 정보"""
    
    id: str = Field(
        ...,
        description="엔티티 ID입니다."
    )
    name: str = Field(
        ...,
        description="엔티티 이름입니다.",
        examples=["JDONE"]
    )
    type: str = Field(
        ...,
        description="엔티티 유형입니다.",
        examples=["ORGANIZATION"]
    )
    description: Optional[str] = Field(
        None,
        description="엔티티 설명입니다."
    )
    mention_count: Optional[int] = Field(
        None,
        description="문서에서 언급된 횟수입니다.",
        examples=[5]
    )
    community_id: Optional[int] = Field(
        None,
        description="소속 커뮤니티 ID입니다."
    )

class RelationshipInfo(BaseModel):
    """관계 정보"""
    
    source_id: str = Field(
        ...,
        description="출발 엔티티 ID입니다."
    )
    source_name: str = Field(
        ...,
        description="출발 엔티티 이름입니다.",
        examples=["JDONE"]
    )
    source_type: str = Field(
        ...,
        description="출발 엔티티 유형입니다.",
        examples=["ORGANIZATION"]
    )
    target_id: str = Field(
        ...,
        description="도착 엔티티 ID입니다."
    )
    target_name: str = Field(
        ...,
        description="도착 엔티티 이름입니다.",
        examples=["GraphRAG"]
    )
    target_type: str = Field(
        ...,
        description="도착 엔티티 유형입니다.",
        examples=["TECHNOLOGY"]
    )
    relation_type: str = Field(
        ...,
        description="관계 유형입니다.",
        examples=["CREATED_BY"]
    )
    description: Optional[str] = Field(
        None,
        description="관계 설명입니다."
    )

class CommunityInfo(BaseModel):
    """커뮤니티 정보"""
    
    id: int = Field(
        ...,
        description="커뮤니티 ID입니다.",
        examples=[0]
    )
    summary: Optional[str] = Field(
        None,
        description="커뮤니티 요약입니다."
    )
    member_count: Optional[int] = Field(
        None,
        description="소속 엔티티 수입니다.",
        examples=[5]
    )

class DocumentChunk(BaseModel):
    """문서 청크 정보"""
    
    document_id: int = Field(
        ...,
        description="문서 ID입니다."
    )
    file_name: str = Field(
        ...,
        description="파일명입니다."
    )
    chunk_index: int = Field(
        ...,
        description="청크 인덱스입니다."
    )
    content: str = Field(
        ...,
        description="청크 내용입니다."
    )
    page: Optional[int] = Field(
        None,
        description="해당 내용이 위치한 문서 페이지 번호입니다.",
        examples=[1]
    )

class GraphSearchResponse(BaseModel):
    """GraphRAG 검색 응답"""
    
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
    entities: list[EntityInfo] = Field(
        ...,
        description="검색된 엔티티 리스트입니다."
    )
    relationships: list[RelationshipInfo] = Field(
        ...,
        description="검색된 관계 리스트입니다."
    )
    communities: list[CommunityInfo] = Field(
        ...,
        description="관련 커뮤니티 리스트입니다."
    )
    documents: list[DocumentChunk] = Field(
        default=[],
        description="관련 문서 청크 리스트입니다."
    )


class VectorSearchResult(BaseModel):
    """벡터 검색 결과 아이템"""
    
    document_id: int = Field(
        ...,
        description="문서 ID입니다."
    )
    file_name: str = Field(
        ...,
        description="파일명입니다."
    )
    chunk_index: int = Field(
        ...,
        description="청크 인덱스입니다."
    )
    content: str = Field(
        ...,
        description="청크 내용입니다."
    )
    page: Optional[int] = Field(
        None,
        description="해당 내용이 위치한 문서 페이지 번호입니다.",
        examples=[1]
    )
    score: float = Field(
        ...,
        description="유사도 점수입니다."
    )

class HybridSearchResponse(BaseModel):
    """하이브리드 검색 응답"""
    
    query: str = Field(
        ...,
        description="검색 쿼리입니다."
    )
    collection_name: str = Field(
        ...,
        description="검색한 컬렉션 이름입니다."
    )
    vector_results: list[VectorSearchResult] = Field(
        ...,
        description="벡터 검색 결과입니다."
    )
    entities: list[EntityInfo] = Field(
        ...,
        description="관련 엔티티 리스트입니다."
    )
    relationships: list[RelationshipInfo] = Field(
        ...,
        description="관련 관계 리스트입니다."
    )


class GraphNode(BaseModel):
    """그래프 노드 (엔티티)"""
    
    id: str = Field(
        ...,
        description="노드 ID입니다."
    )
    label: str = Field(
        ...,
        description="노드 레이블 (엔티티 이름)입니다.",
        examples=["JDONE"]
    )
    type: str = Field(
        ...,
        description="노드 타입 (엔티티 유형)입니다.",
        examples=["ORGANIZATION"]
    )
    description: Optional[str] = Field(
        None,
        description="노드 설명입니다."
    )
    size: int = Field(
        1,
        description="노드 크기 (언급 횟수 기반)입니다.",
        examples=[5]
    )
    community_id: Optional[int] = Field(
        None,
        description="소속 커뮤니티 ID입니다."
    )
    document_ids: list[int] = Field(
        default_factory=list,
        description="관련 문서 ID 리스트입니다."
    )

class GraphEdge(BaseModel):
    """그래프 엣지 (관계)"""
    
    id: str = Field(
        ...,
        description="엣지 ID입니다."
    )
    source: str = Field(
        ...,
        description="출발 노드 ID입니다."
    )
    target: str = Field(
        ...,
        description="도착 노드 ID입니다."
    )
    label: str = Field(
        ...,
        description="엣지 레이블 (관계 유형)입니다.",
        examples=["WORKS_FOR"]
    )
    description: Optional[str] = Field(
        None,
        description="관계 설명입니다."
    )
    weight: int = Field(
        1,
        description="엣지 가중치입니다.",
        examples=[3]
    )

class GraphVisualizationResponse(BaseModel):
    """그래프 시각화 응답"""
    
    collection_name: str = Field(
        ...,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    nodes: list[GraphNode] = Field(
        ...,
        description="그래프 노드 (엔티티) 리스트입니다."
    )
    edges: list[GraphEdge] = Field(
        ...,
        description="그래프 엣지 (관계) 리스트입니다."
    )
    communities: list[CommunityInfo] = Field(
        default_factory=list,
        description="커뮤니티 정보 리스트입니다."
    )
    stats: dict = Field(
        ...,
        description="그래프 통계입니다.",
        examples=[{
            "node_count": 50,
            "edge_count": 75,
            "community_count": 5
        }]
    )

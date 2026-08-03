from typing import Optional, Any

from pydantic import BaseModel, Field


class Entity(BaseModel):
    """추출된 엔티티"""
    
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
        description="엔티티 설명입니다.",
        examples=["AI 기반 서비스 개발 회사"]
    )

class Relationship(BaseModel):
    """추출된 관계"""
    
    source: str = Field(
        ...,
        description="소스 엔티티 이름입니다.",
        examples=["JDONE"]
    )
    target: str = Field(
        ...,
        description="타겟 엔티티 이름입니다.",
        examples=["Microsoft"]
    )
    relation_type: str = Field(
        ...,
        description="관계 유형입니다.",
        examples=["WORKS_FOR"]
    )
    description: Optional[str] = Field(
        None,
        description="관계에 대한 설명입니다.",
        examples=["JDONE는 AI 서비스 개발 회사입니다."]
    )

class ExtractionResult(BaseModel):
    """엔티티/관계 추출 결과"""
    
    entities: list[Entity] = Field(
        ...,
        description="추출된 엔티티 목록입니다."
    )
    relationships: list[Relationship] = Field(
        ...,
        description="추출된 관계 목록입니다."
    )


class GraphSearchResult(BaseModel):
    """그래프 검색 결과"""
    
    entities: list[dict[str, Any]] = Field(
        ...,
        description="검색된 엔티티 목록입니다."
    )
    relationships: list[dict[str, Any]] = Field(
        ...,
        description="검색된 관계 목록입니다."
    )
    communities: list[dict[str, Any]] = Field(
        ...,
        description="검색된 커뮤니티 목록입니다."
    )
    documents: list[dict[str, Any]] = Field(
        ...,
        description="관련 문서 청크 목록입니다."
    )

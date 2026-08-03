from pydantic import BaseModel, Field

from typing import Optional, Literal


class GraphIndexPayload(BaseModel):
    """GraphRAG 인덱싱 요청"""
    
    collection_name: str = Field(
        ...,
        description="인덱싱할 컬렉션 이름입니다.",
        examples=["test"]
    )
    text_model: str = Field(
        ...,
        description=(
            "엔티티/관계 추출에 사용할 텍스트 생성 모델명입니다.  \n"
            "모델 목록에 등록된 활성화된 텍스트 생성 모델이어야 합니다."
        ),
        examples=["gpt-4o-mini"]
    )
    generate_summaries: bool = Field(
        True,
        description="커뮤니티 요약 생성 여부입니다.",
        examples=[True, False]
    )
    background: bool = Field(
        False,
        description=(
            "백그라운드 처리 여부입니다.  \n"
            "`true`로 설정하면 요청 후 즉시 응답하며, 인덱싱은 백그라운드에서 진행됩니다.  \n"
            "`false`로 설정하면 인덱싱이 완료될 때까지 대기합니다.  \n"
            "인덱싱 상태는 컬렉션 목록에서 확인할 수 있습니다."
        ),
        examples=[True, False]
    )


class GraphSearchPayload(BaseModel):
    """GraphRAG 검색 요청"""
    
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
    text_model: str = Field(
        ...,
        description=(
            "검색에 사용할 텍스트 생성 모델명입니다.  \n"
            "검색 시 쿼리 엔티티 추출에 사용됩니다."
        ),
        examples=["gpt-4o-mini"]
    )
    top_k: Optional[int] = Field(
        10,
        ge=1,
        le=50,
        description="검색 시 반환할 결과 개수입니다. (기본값: 10)",
        examples=[10, 20]
    )


class FacilitySyncItem(BaseModel):
    """동기화할 시설(Facility) 항목"""

    facility_id: int = Field(
        ...,
        description="pet-pass-one 시설 ID입니다.",
        examples=[1]
    )
    name: str = Field(
        ...,
        description="시설명입니다.",
        examples=["용인동물병원"]
    )
    facility_type: str = Field(
        ...,
        description="시설 유형입니다.",
        examples=["동물병원"]
    )
    address: Optional[str] = Field(
        None,
        description="주소입니다."
    )
    phone: Optional[str] = Field(
        None,
        description="연락처입니다."
    )


class FacilitySyncPayload(BaseModel):
    """시설 데이터 동기화 요청 (구조화 데이터, LLM 미사용)"""

    collection_name: str = Field(
        ...,
        description="적재할 컬렉션 이름입니다. 존재하지 않으면 시스템 컬렉션으로 자동 생성됩니다.",
        examples=["pet-pass-one-facilities"]
    )
    facilities: list[FacilitySyncItem] = Field(
        ...,
        description="동기화할 시설 목록입니다. (호출할 때마다 전체 upsert)"
    )


class GraphKeywordSearchPayload(BaseModel):
    """키워드 기반 그래프 검색 요청 (LLM 미사용, 구조화 데이터용)"""

    collection_name: str = Field(
        ...,
        description="검색할 컬렉션 이름입니다.",
        examples=["pet-pass-one-facilities"]
    )
    keywords: list[str] = Field(
        ...,
        description="매칭할 키워드 목록입니다.",
        examples=[["동물병원"]]
    )
    top_k: Optional[int] = Field(
        10,
        ge=1,
        le=50,
        description="검색 시 반환할 결과 개수입니다. (기본값: 10)",
        examples=[10, 20]
    )


class HybridSearchPayload(BaseModel):
    """하이브리드 검색 요청 (Vector + Graph)"""
    
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
    text_model: str = Field(
        ...,
        description="그래프 검색에 사용할 텍스트 생성 모델명입니다.",
        examples=["gpt-4o-mini"]
    )
    vector_top_k: Optional[int] = Field(
        5,
        ge=1,
        le=50,
        description="벡터 검색 결과 개수입니다. (기본값: 5)",
        examples=[5, 10]
    )
    graph_top_k: Optional[int] = Field(
        10,
        ge=1,
        le=50,
        description="그래프 검색 결과 개수입니다. (기본값: 10)",
        examples=[10, 20]
    )
    score_threshold: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="벡터 검색 최소 유사도 점수입니다. (0.0 ~ 1.0)",
        examples=[0.7, 0.8]
    )

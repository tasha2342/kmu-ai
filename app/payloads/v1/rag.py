from pydantic import BaseModel, Field

from typing import Optional


class SyncRagItemsPayload(BaseModel):
    """지식베이스 동기화(재색인) 요청"""

    names: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "동기화할 지식베이스(컬렉션) 이름 목록입니다.  \n"
            "`kmu_faq_knowledge` → FAQ 재색인, `kmu_regulations` → 학칙·규정 재색인.  \n"
            "그 외 업로드 문서 컬렉션은 현재 원천 연계 재색인을 지원하지 않습니다."
        ),
        examples=[["kmu_faq_knowledge"], ["kmu_regulations", "kmu_faq_knowledge"]]
    )
    force: bool = Field(
        False,
        description="원문 변경이 없어도 강제로 재색인할지 여부입니다.",
        examples=[False, True]
    )
    only_stale: bool = Field(
        True,
        description="재색인이 필요한 항목만 대상으로 할지 여부입니다. (FAQ 기준)",
        examples=[True, False]
    )


class DeleteRagItemsPayload(BaseModel):
    """지식베이스 일괄 삭제 요청"""

    names: list[str] = Field(
        ...,
        min_length=1,
        description="삭제할 지식베이스(컬렉션) 이름 목록입니다.",
        examples=[["demo_docs"], ["old_kb_1", "old_kb_2"]]
    )


class UpdateRagActivePayload(BaseModel):
    """지식베이스 활성 토글 요청"""

    is_active: bool = Field(
        ...,
        description="검색(RAG) 활성 여부입니다.",
        examples=[True, False]
    )


class UpdateRagItemPayload(BaseModel):
    """지식베이스 메타 정보 수정 요청"""

    description: Optional[str] = Field(
        None,
        max_length=1024,
        description="지식베이스 설명입니다."
    )
    is_active: Optional[bool] = Field(
        None,
        description="검색(RAG) 활성 여부입니다. 요청에 포함된 경우에만 변경됩니다.",
        examples=[True, False]
    )
    chunk_size: Optional[int] = Field(
        None,
        ge=100,
        le=10000,
        description="문서 청킹 목표 크기(문자 수)입니다.",
        examples=[1000]
    )
    chunk_overlap: Optional[int] = Field(
        None,
        ge=0,
        le=5000,
        description="청크 간 오버랩 크기(문자 수)입니다.",
        examples=[100]
    )
    top_k: Optional[int] = Field(
        None,
        ge=1,
        le=50,
        description="검색 상위 K입니다.",
        examples=[5]
    )
    similarity_threshold: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="검색 최소 유사도(score_threshold)입니다.",
        examples=[0.35]
    )

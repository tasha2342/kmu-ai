from pydantic import BaseModel, Field

from typing import Optional
from uuid import UUID

from app.models.enum import RagEmbeddingStatus, SourceType, IngestionStatus


class RagItemInfo(BaseModel):
    """RAG 관리 목록/상세용 지식베이스 항목"""

    name: str = Field(
        ...,
        description="지식베이스(컬렉션) 고유 이름입니다.",
        examples=["kmu_regulations"]
    )
    display_name: str = Field(
        ...,
        description="화면 표시명(한글)입니다.",
        examples=["대학 규정집"]
    )
    display_name_en: Optional[str] = Field(
        None,
        description="화면 표시명(영문)입니다.",
        examples=["Uni Regulations"]
    )
    bot_label: Optional[str] = Field(
        None,
        description="연결된 봇 표시명입니다.",
        examples=["학생 봇"]
    )
    bot_label_en: Optional[str] = Field(
        None,
        description="연결된 봇 영문 표시명입니다.",
        examples=["Student Bot"]
    )
    source_type: Optional[SourceType] = Field(
        None,
        description="원천 유형입니다. 관리형 KB만 값이 있습니다.",
        examples=list(SourceType)
    )
    document_count: int = Field(
        0,
        description="문서(또는 FAQ 항목) 수입니다.",
        examples=[512]
    )
    chunk_count: int = Field(
        0,
        description="벡터 청크(또는 FAQ 임베딩) 수입니다.",
        examples=[12500]
    )
    embedding_status: RagEmbeddingStatus = Field(
        RagEmbeddingStatus.SUCCESS,
        description="임베딩/동기화 상태입니다.",
        examples=list(RagEmbeddingStatus)
    )
    embedding_status_label: str = Field(
        ...,
        description="임베딩 상태 한글 라벨입니다.",
        examples=["동기화 완료", "동기화 중", "에러"]
    )
    last_synced_at: Optional[str] = Field(
        None,
        description="최근 동기화 일시입니다."
    )
    is_active: bool = Field(
        True,
        description="검색(RAG) 활성 여부입니다.",
        examples=[True, False]
    )
    vector_db: str = Field(
        "pgvector",
        description="벡터 저장소 종류입니다.",
        examples=["pgvector"]
    )
    embedding_model: str = Field(
        ...,
        description="임베딩 모델명입니다.",
        examples=["text-embedding-3-small"]
    )
    vector_size: int = Field(
        ...,
        description="벡터 차원입니다.",
        examples=[1024]
    )
    description: Optional[str] = Field(
        None,
        description="지식베이스 설명입니다."
    )
    is_system: bool = Field(
        False,
        description="시스템 전용 지식베이스 여부입니다."
    )
    chunk_size: int = Field(
        1000,
        description="문서 청킹 목표 크기(문자 수)입니다.",
        examples=[1000]
    )
    chunk_overlap: int = Field(
        100,
        description="청크 간 오버랩 크기(문자 수)입니다.",
        examples=[100]
    )
    top_k: int = Field(
        5,
        description="검색 상위 K입니다.",
        examples=[5]
    )
    similarity_threshold: float = Field(
        0.35,
        description="검색 최소 유사도(score_threshold)입니다.",
        examples=[0.35]
    )
    created_at: str = Field(
        ...,
        description="생성 일시입니다."
    )
    updated_at: str = Field(
        ...,
        description="수정 일시입니다."
    )


class RagItemDetailResponse(RagItemInfo):
    """RAG 관리 상세 패널 응답"""

    recent_error: Optional[str] = Field(
        None,
        description="최근 오류 메시지입니다. 없으면 null입니다.",
        examples=[None, "임베딩 모델 호출 실패"]
    )
    processing_document_count: int = Field(
        0,
        description="처리 중인 문서 수입니다.",
        examples=[0]
    )
    error_document_count: int = Field(
        0,
        description="오류 상태 문서 수입니다.",
        examples=[0]
    )
    last_job_id: Optional[UUID] = Field(
        None,
        description="최근 수집(재색인) 작업 ID입니다."
    )
    last_job_status: Optional[IngestionStatus] = Field(
        None,
        description="최근 수집 작업 상태입니다.",
        examples=list(IngestionStatus)
    )


class RagSyncResultItem(BaseModel):
    """지식베이스 동기화 결과 항목"""

    name: str = Field(..., description="지식베이스 이름입니다.")
    success: bool = Field(..., description="동기화 요청 성공 여부입니다.")
    message: str = Field(..., description="결과 메시지입니다.")
    job_id: Optional[UUID] = Field(
        None,
        description="시작된 수집 작업 ID입니다. (관리형 KB만)"
    )


class RagSyncResponse(BaseModel):
    """지식베이스 동기화 응답"""

    message: str = Field(..., description="요약 메시지입니다.")
    results: list[RagSyncResultItem] = Field(..., description="항목별 결과입니다.")


class RagDeleteResultItem(BaseModel):
    """지식베이스 삭제 결과 항목"""

    name: str = Field(..., description="지식베이스 이름입니다.")
    success: bool = Field(..., description="삭제 성공 여부입니다.")
    message: str = Field(..., description="결과 메시지입니다.")


class RagDeleteResponse(BaseModel):
    """지식베이스 일괄 삭제 응답"""

    message: str = Field(..., description="요약 메시지입니다.")
    results: list[RagDeleteResultItem] = Field(..., description="항목별 결과입니다.")


class RagLogItem(BaseModel):
    """지식베이스 동기화/처리 로그 항목"""

    id: str = Field(..., description="로그 ID입니다.")
    event_type: str = Field(
        ...,
        description="이벤트 유형입니다. (`ingestion_job` | `document_error`)",
        examples=["ingestion_job", "document_error"]
    )
    status: str = Field(..., description="상태 값입니다.")
    message: Optional[str] = Field(None, description="메시지입니다.")
    created_at: Optional[str] = Field(None, description="발생 일시입니다.")
    meta: Optional[dict] = Field(None, description="부가 정보입니다.")

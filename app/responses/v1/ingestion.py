from pydantic import BaseModel, Field

from typing import Optional

from uuid import UUID

from app.models.enum import SourceType, IngestionStatus, IngestionItemStatus


class IngestionJobInfo(BaseModel):
    """수집 작업 정보 (KAI-REQ-014)"""

    id: UUID = Field(
        ...,
        description="수집 작업 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    source_type: SourceType = Field(
        ...,
        description="원천 유형입니다.",
        examples=list(SourceType)
    )
    status: IngestionStatus = Field(
        ...,
        description="작업 상태입니다.",
        examples=list(IngestionStatus)
    )
    total_count: int = Field(
        0,
        description="전체 항목 수입니다.",
        examples=[120]
    )
    success_count: int = Field(
        0,
        description=(
            "색인에 성공한 항목 수입니다.  \n"
            "원문 변경이 없어 건너뛴 항목은 포함되지 않습니다."
        ),
        examples=[118]
    )
    failed_count: int = Field(
        0,
        description="색인에 실패한 항목 수입니다.",
        examples=[2]
    )
    error_message: Optional[str] = Field(
        None,
        description="작업 단위 오류 메시지입니다.",
        examples=[None, "2건의 항목 색인에 실패했습니다."]
    )
    started_at: str = Field(
        ...,
        description="시작 일시입니다."
    )
    ended_at: Optional[str] = Field(
        None,
        description=(
            "종료 일시입니다.  \n"
            "작업이 진행 중이면 `null`입니다."
        )
    )

class IngestionJobItemSummary(BaseModel):
    """수집 작업 항목 요약"""

    total: int = Field(
        0,
        description="기록된 전체 항목 수입니다.",
        examples=[120]
    )
    success: int = Field(
        0,
        description="색인에 성공한 항목 수입니다.",
        examples=[100]
    )
    failed: int = Field(
        0,
        description="색인에 실패한 항목 수입니다.",
        examples=[2]
    )
    skipped: int = Field(
        0,
        description="원문 변경이 없어 건너뛴 항목 수입니다.",
        examples=[18]
    )
    pending: int = Field(
        0,
        description="아직 처리되지 않은 항목 수입니다.",
        examples=[0]
    )

class IngestionJobDetailResponse(IngestionJobInfo):
    """수집 작업 상세 응답"""

    item_summary: IngestionJobItemSummary = Field(
        ...,
        description="처리 상태별 항목 수 요약입니다."
    )

class IngestionJobItemInfo(BaseModel):
    """수집 작업 항목 정보"""

    id: UUID = Field(
        ...,
        description="수집 작업 항목 ID입니다.",
        examples=["1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"]
    )
    job_id: UUID = Field(
        ...,
        description="수집 작업 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    source_table: str = Field(
        ...,
        description=(
            "원천 테이블명입니다.  \n"
            "FAQ 재색인은 `faq_items`, 학칙·규정 재색인은 `documents`입니다."
        ),
        examples=["faq_items", "documents"]
    )
    source_id: UUID = Field(
        ...,
        description=(
            "원천 데이터 ID입니다.  \n"
            "FAQ 재색인은 `faq_items.id`입니다.  \n"
            "학칙·규정은 원천이 DB 행이 아니라 HWP 파일이라 UUID PK가 없으므로, "
            "파일명에서 만든 결정론적 UUID를 사용합니다. (같은 파일은 항상 같은 값)"
        ),
        examples=["3f2b1a09-8c7d-6e5f-4a3b-2c1d0e9f8a7b"]
    )
    status: IngestionItemStatus = Field(
        ...,
        description="처리 상태입니다.",
        examples=list(IngestionItemStatus)
    )
    error_message: Optional[str] = Field(
        None,
        description="항목 단위 오류 메시지입니다."
    )
    created_at: str = Field(
        ...,
        description="생성 일시입니다."
    )

class RunIngestionResponse(BaseModel):
    """수집 작업 실행 응답"""

    message: str = Field(
        ...,
        description="응답 메시지입니다.",
        examples=["FAQ 재색인 작업이 시작되었습니다."]
    )
    id: UUID = Field(
        ...,
        description=(
            "생성된 수집 작업 ID입니다.  \n"
            "`GET /ingestion/job/{job_id}`로 진행 상태를 확인할 수 있습니다."
        ),
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    source_type: SourceType = Field(
        ...,
        description="원천 유형입니다.",
        examples=list(SourceType)
    )
    status: IngestionStatus = Field(
        ...,
        description=(
            "작업 상태입니다.  \n"
            "실제 색인은 백그라운드에서 진행되므로 응답 시점에는 항상 `running`입니다."
        ),
        examples=list(IngestionStatus)
    )

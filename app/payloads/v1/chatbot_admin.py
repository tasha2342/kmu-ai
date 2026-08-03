from uuid import UUID

from typing import Optional

from pydantic import BaseModel, Field

from app.models.enum import ReviewStatus


class UpdateUnansweredQuestionPayload(BaseModel):
    """미응답 질문 검토 상태 변경 요청"""

    review_status: ReviewStatus = Field(
        ...,
        description=(
            "변경할 검토 상태입니다.  \n"
            "- **pending**: 검토 대기  \n"
            "- **in_review**: 검토 중  \n"
            "- **resolved**: 조치 완료 (FAQ 등록 등)  \n"
            "- **rejected**: 조치 불필요  \n"
        ),
        examples=list(ReviewStatus)
    )


class AddUserFeedbackPayload(BaseModel):
    """사용자 피드백 등록 요청"""

    message_id: UUID = Field(
        ...,
        description=(
            "평가할 챗봇 응답 메시지 ID입니다.  \n"
            "본인 세션의 챗봇(assistant) 메시지만 평가할 수 있습니다."
        ),
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    rating: int = Field(
        ..., ge=1, le=5,
        description="만족도 평점입니다. (1~5, 높을수록 만족)",
        examples=[5]
    )
    feedback_text: Optional[str] = Field(
        None, max_length=2000,
        description="피드백 내용입니다.",
        examples=["원하는 답변을 정확히 받았습니다."]
    )

from datetime import datetime

from uuid import UUID

from typing import Optional

from pydantic import BaseModel, Field, field_serializer

from app.models.enum import ChatIntent, ChatRole

import app.utils.common as util


class DailyQuestionTrend(BaseModel):
    """일자별 질문 추이"""

    date: str = Field(
        ...,
        description="날짜입니다. (YYYY-MM-DD)",
        examples=["2026-07-01"]
    )
    question_count: int = Field(
        ...,
        description="해당 일자의 질문 수입니다.",
        examples=[128]
    )
    unanswered_count: int = Field(
        ...,
        description="해당 일자의 미응답 건수입니다.",
        examples=[7]
    )

class ChatbotStatsResponse(BaseModel):
    """챗봇 이용 통계 응답 (KAI-REQ-031)"""

    start_date: str = Field(
        ...,
        description="조회 시작일입니다. (YYYY-MM-DD)",
        examples=["2026-07-01"]
    )
    end_date: str = Field(
        ...,
        description="조회 종료일입니다. (YYYY-MM-DD)",
        examples=["2026-07-27"]
    )
    total_questions: int = Field(
        ...,
        description="총 질문 수입니다. (사용자 메시지 기준)",
        examples=[3200]
    )
    total_sessions: int = Field(
        ...,
        description="총 세션 수입니다. (기간 내 생성된 세션 기준)",
        examples=[840]
    )
    active_users: int = Field(
        ...,
        description="활성 사용자 수입니다. (기간 내 세션을 생성한 고유 사용자 수)",
        examples=[312]
    )
    avg_response_time_ms: float = Field(
        ...,
        description=(
            "평균 응답시간(ms)입니다.  \n"
            "챗봇(assistant) 메시지의 `latency_ms` 평균이며, 값이 0인 메시지는 제외합니다."
        ),
        examples=[1350.25]
    )
    unanswered_count: int = Field(
        ...,
        description="미응답 건수입니다.",
        examples=[96]
    )
    unanswered_rate: float = Field(
        ...,
        description="미응답률 (%)입니다. (미응답 건수 / 총 질문 수)",
        examples=[3.0]
    )
    feedback_count: int = Field(
        ...,
        description="만족도 평가 건수입니다.",
        examples=[420]
    )
    average_rating: Optional[float] = Field(
        None,
        description="평균 만족도입니다. (1~5, 평가가 없으면 null)",
        examples=[4.35]
    )
    daily_trend: list[DailyQuestionTrend] = Field(
        ...,
        description="일자별 질문 추이 목록입니다."
    )


class KeywordStat(BaseModel):
    """인기 키워드 통계"""

    keyword: str = Field(
        ...,
        description="키워드입니다.",
        examples=["수강신청"]
    )
    count: int = Field(
        ...,
        description="출현 횟수입니다.",
        examples=[214]
    )

class KeywordStatsResponse(BaseModel):
    """인기 키워드 통계 응답 (KAI-REQ-031)"""

    start_date: str = Field(
        ...,
        description="조회 시작일입니다. (YYYY-MM-DD)",
        examples=["2026-07-01"]
    )
    end_date: str = Field(
        ...,
        description="조회 종료일입니다. (YYYY-MM-DD)",
        examples=["2026-07-27"]
    )
    total_queries: int = Field(
        ...,
        description="집계 대상 질의 수입니다. (검색 로그 건수)",
        examples=[3200]
    )
    keywords: list[KeywordStat] = Field(
        ...,
        description="출현 횟수 기준 상위 키워드 목록입니다."
    )


class IntentStat(BaseModel):
    """의도별 질문 분포"""

    intent: ChatIntent = Field(
        ...,
        description="감지 의도입니다.",
        examples=list(ChatIntent)
    )
    count: int = Field(
        ...,
        description="질문 수입니다.",
        examples=[820]
    )
    ratio: float = Field(
        ...,
        description="전체 대비 비율 (%)입니다.",
        examples=[25.6]
    )

class IntentStatsResponse(BaseModel):
    """의도별 질문 분포 응답 (KAI-REQ-031)"""

    start_date: str = Field(
        ...,
        description="조회 시작일입니다. (YYYY-MM-DD)",
        examples=["2026-07-01"]
    )
    end_date: str = Field(
        ...,
        description="조회 종료일입니다. (YYYY-MM-DD)",
        examples=["2026-07-27"]
    )
    total_count: int = Field(
        ...,
        description="의도가 감지된 질문 수입니다.",
        examples=[3100]
    )
    undetected_count: int = Field(
        ...,
        description="의도가 기록되지 않은 질문 수입니다. (`detected_intent`가 null)",
        examples=[100]
    )
    intents: list[IntentStat] = Field(
        ...,
        description=(
            "의도별 질문 분포 목록입니다.  \n"
            "집계 결과가 없는 의도도 0건으로 포함됩니다."
        )
    )


class FeedbackRatingBucket(BaseModel):
    """만족도 평점 분포 항목"""

    rating: int = Field(
        ...,
        description="평점입니다. (1~5)",
        examples=[5]
    )
    count: int = Field(
        ...,
        description="해당 평점의 건수입니다.",
        examples=[210]
    )
    ratio: float = Field(
        ...,
        description="전체 대비 비율 (%)입니다.",
        examples=[50.0]
    )

class DailyRatingTrend(BaseModel):
    """일자별 만족도 추이"""

    date: str = Field(
        ...,
        description="날짜입니다. (YYYY-MM-DD)",
        examples=["2026-07-01"]
    )
    count: int = Field(
        ...,
        description="해당 일자의 평가 건수입니다.",
        examples=[18]
    )
    average_rating: float = Field(
        ...,
        description="해당 일자의 평균 만족도입니다.",
        examples=[4.28]
    )

class FeedbackStatsResponse(BaseModel):
    """만족도 통계 응답 (KAI-REQ-033)"""

    start_date: str = Field(
        ...,
        description="조회 시작일입니다. (YYYY-MM-DD)",
        examples=["2026-07-01"]
    )
    end_date: str = Field(
        ...,
        description="조회 종료일입니다. (YYYY-MM-DD)",
        examples=["2026-07-27"]
    )
    total_count: int = Field(
        ...,
        description="총 평가 건수입니다.",
        examples=[420]
    )
    average_rating: Optional[float] = Field(
        None,
        description="평균 만족도입니다. (1~5, 평가가 없으면 null)",
        examples=[4.35]
    )
    distribution: list[FeedbackRatingBucket] = Field(
        ...,
        description=(
            "평점 분포 목록입니다.  \n"
            "1~5점이 모두 포함되며, 건수가 없으면 0건으로 반환됩니다."
        )
    )
    daily_trend: list[DailyRatingTrend] = Field(
        ...,
        description="일자별 만족도 추이 목록입니다."
    )


class UserFeedbackListItem(BaseModel):
    """사용자 피드백 목록 항목"""

    id: UUID = Field(
        ...,
        description="피드백 ID입니다."
    )
    session_id: UUID = Field(
        ...,
        description="세션 ID입니다."
    )
    message_id: UUID = Field(
        ...,
        description="메시지 ID입니다."
    )
    user_name: Optional[str] = Field(
        None,
        description="세션 소유 사용자명입니다.",
        examples=["20241234"]
    )
    rating: int = Field(
        ...,
        description="평점입니다. (1~5)",
        examples=[5]
    )
    feedback_text: Optional[str] = Field(
        None,
        description="피드백 내용입니다.",
        examples=["원하는 답변을 정확히 받았습니다."]
    )
    created_at: datetime = Field(
        ...,
        description="등록 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)


class ConversationLogItem(BaseModel):
    """대화 이력 로그 항목 (KAI-REQ-044)"""

    id: UUID = Field(
        ...,
        description="메시지 ID입니다."
    )
    session_id: UUID = Field(
        ...,
        description="세션 ID입니다."
    )
    user_name: Optional[str] = Field(
        None,
        description="세션 소유 사용자명입니다.",
        examples=["20241234"]
    )
    role: ChatRole = Field(
        ...,
        description="메시지 역할입니다.",
        examples=list(ChatRole)
    )
    content: str = Field(
        ...,
        description="메시지 내용입니다.",
        examples=["수강신청 정정 기간은 언제인가요?"]
    )
    detected_intent: Optional[ChatIntent] = Field(
        None,
        description="감지 의도입니다.",
        examples=list(ChatIntent)
    )
    model_name: Optional[str] = Field(
        None,
        description="응답 생성에 사용한 모델명입니다.",
        examples=["gemini-2.5-flash"]
    )
    latency_ms: int = Field(
        0,
        description="응답 생성 시간(ms)입니다.",
        examples=[1350]
    )
    is_answered: bool = Field(
        True,
        description="응답 성공 여부입니다.",
        examples=[True, False]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)


class UserUsageLogItem(BaseModel):
    """사용자별 이용 로그 항목 (KAI-REQ-045)"""

    user_name: str = Field(
        ...,
        description="사용자명입니다.",
        examples=["20241234"]
    )
    session_count: int = Field(
        ...,
        description="세션 수입니다.",
        examples=[12]
    )
    question_count: int = Field(
        ...,
        description="질문 수입니다. (사용자 메시지 기준)",
        examples=[48]
    )
    feedback_count: int = Field(
        ...,
        description="만족도 평가 건수입니다.",
        examples=[6]
    )
    average_rating: Optional[float] = Field(
        None,
        description="평균 만족도입니다. (1~5, 평가가 없으면 null)",
        examples=[4.5]
    )
    last_active_at: datetime = Field(
        ...,
        description="최근 활동 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("last_active_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

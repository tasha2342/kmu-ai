from datetime import datetime

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from app.models.enum import (
    ChatIntent,
    ChatLanguage,
    ChatRole,
    ChatSessionStatus,
    UnansweredReason,
)
import app.models.db_item as db_items
import app.utils.common as util


class ChatSessionItem(BaseModel):
    """대화 세션 항목"""

    id: UUID = Field(
        ...,
        description="세션 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    user_name: str = Field(
        ...,
        description="사용자명입니다.",
        examples=["20241234"]
    )
    title: Optional[str] = Field(
        None,
        description="세션 제목입니다.",
        examples=["수강신청 문의"]
    )
    language: ChatLanguage = Field(
        ChatLanguage.AUTO,
        description="대화 언어입니다. (`auto`면 질문마다 언어를 감지해 답변)",
        examples=list(ChatLanguage)
    )
    status: ChatSessionStatus = Field(
        ChatSessionStatus.ACTIVE,
        description="세션 상태입니다.",
        examples=list(ChatSessionStatus)
    )
    message_count: int = Field(
        0,
        description="메시지 수입니다.",
        examples=[4]
    )
    summary: Optional[str] = Field(
        None,
        description="누적 대화 요약입니다. (KAI-REQ-041)"
    )
    last_active_at: datetime = Field(
        ...,
        description="마지막 활동 일시입니다.",
        examples=[util.get_now()]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("last_active_at", "created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

    @classmethod
    def from_session(cls, session: db_items.ChatSession) -> "ChatSessionItem":
        """ChatSession 아이템을 목록/상세 응답 항목으로 변환합니다."""

        return cls(
            id=session.id,
            user_name=session.user_name,
            title=session.title,
            language=session.language,
            status=session.status,
            message_count=session.message_count,
            summary=session.summary,
            last_active_at=session.last_active_at,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )


class ChatSourceItem(BaseModel):
    """응답 근거 항목 (KAI-REQ-015)"""

    source_type: str = Field(
        "faq",
        description="근거 유형입니다.",
        examples=["faq"]
    )
    source_id: str = Field(
        ...,
        description="근거 원문 ID입니다. (FAQ ID)",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    question: Optional[str] = Field(
        None,
        description="근거 FAQ의 대표 질문입니다.",
        examples=["수강신청 기간은 언제인가요?"]
    )
    category_code: Optional[str] = Field(
        None,
        description="카테고리 코드입니다.",
        examples=["ACADEMIC_REGISTRATION"]
    )
    department_code: Optional[str] = Field(
        None,
        description="담당 부서 코드입니다.",
        examples=["ACADEMIC_SUPPORT"]
    )
    source_url: Optional[str] = Field(
        None,
        description="원문 URL입니다.",
        examples=["https://www.kmu.ac.kr/notice/1234"]
    )
    score: Optional[float] = Field(
        None,
        description="유사도 점수입니다.",
        examples=[0.91]
    )


class ChatMessageItem(BaseModel):
    """대화 메시지 항목"""

    id: UUID = Field(
        ...,
        description="메시지 ID입니다.",
        examples=["6f2c9a10-3b4d-4c5e-9f8a-1b2c3d4e5f60"]
    )
    session_id: UUID = Field(
        ...,
        description="세션 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    role: ChatRole = Field(
        ...,
        description="메시지 역할입니다.",
        examples=list(ChatRole)
    )
    content: str = Field(
        ...,
        description="메시지 내용입니다.",
        examples=["2026학년도 1학기 수강신청 기간은 ..."]
    )
    detected_intent: Optional[ChatIntent] = Field(
        None,
        description="감지 의도입니다.",
        examples=list(ChatIntent)
    )
    sources: Optional[list[ChatSourceItem]] = Field(
        None,
        description="응답 근거 목록입니다."
    )
    attachments: Optional[list[dict]] = Field(
        None,
        description="첨부 파일 목록입니다.",
        examples=[[{"file_name": "시간표.png", "file_type": "image/png"}]]
    )
    model_name: Optional[str] = Field(
        None,
        description="응답 생성에 사용한 모델명입니다.",
        examples=["kmu-chat"]
    )
    latency_ms: int = Field(
        0,
        description="응답 생성 시간(ms)입니다.",
        examples=[1350]
    )
    is_answered: bool = Field(
        True,
        description="응답 성공 여부입니다. (KAI-REQ-040)",
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

    @classmethod
    def from_message(cls, message: db_items.ChatMessage) -> "ChatMessageItem":
        """ChatMessage 아이템을 응답 항목으로 변환합니다."""

        return cls(
            id=message.id,
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            detected_intent=message.detected_intent,
            sources=[ChatSourceItem(**source) for source in (message.sources or [])],
            attachments=message.attachments,
            model_name=message.model_name,
            latency_ms=message.latency_ms,
            is_answered=message.is_answered,
            created_at=message.created_at,
        )


class ChatSessionDetailResponse(BaseModel):
    """대화 세션 상세 응답 (KAI-REQ-045 대화 이력 조회)"""

    session: ChatSessionItem = Field(
        ...,
        description="세션 정보입니다."
    )
    messages: list[ChatMessageItem] = Field(
        default_factory=list,
        description="세션의 메시지 이력입니다. (오래된 순)"
    )


class ChatMessageResponse(BaseModel):
    """대화 메시지 전송 응답 (`stream=false`)"""

    session_id: UUID = Field(
        ...,
        description="응답이 저장된 세션 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    message_id: UUID = Field(
        ...,
        description="챗봇 응답 메시지 ID입니다.",
        examples=["6f2c9a10-3b4d-4c5e-9f8a-1b2c3d4e5f60"]
    )
    user_message_id: UUID = Field(
        ...,
        description="사용자 질문 메시지 ID입니다.",
        examples=["1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"]
    )
    answer: str = Field(
        ...,
        description="챗봇 응답 내용입니다.",
        examples=["2026학년도 1학기 수강신청 기간은 ..."]
    )
    detected_intent: ChatIntent = Field(
        ChatIntent.UNKNOWN,
        description="감지 의도입니다. (KAI-REQ-030)",
        examples=list(ChatIntent)
    )
    sources: list[ChatSourceItem] = Field(
        default_factory=list,
        description="응답 근거 목록입니다."
    )
    is_answered: bool = Field(
        True,
        description="응답 성공 여부입니다.",
        examples=[True, False]
    )
    unanswered_reason: Optional[UnansweredReason] = Field(
        None,
        description="미응답 사유입니다. (KAI-REQ-040)",
        examples=list(UnansweredReason)
    )
    notice: Optional[str] = Field(
        None,
        description=(
            "세션 안내 문구입니다.  \n"
            "미입력으로 이전 세션이 자동 종료되어 새 세션에서 답변한 경우 등에 채워집니다. (KAI-REQ-039)"
        ),
        examples=["일정 시간 입력이 없어 대화를 종료합니다. 추가 문의가 있으시면 새로 질문해 주세요."]
    )
    latency_ms: int = Field(
        0,
        description="응답 생성 시간(ms)입니다.",
        examples=[1350]
    )
    created_at: datetime = Field(
        ...,
        description="응답 생성 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)


class ChatStreamEvent(BaseModel):
    """대화 메시지 스트리밍 이벤트 (`stream=true`, SSE)

    `event` 필드에 따라 `data` 형식이 달라집니다.
    """

    event: str = Field(
        ...,
        description=(
            "이벤트 종류입니다.  \n"
            "- **session**: 세션/메시지 ID 등 메타 정보 (스트림 시작 시 1회)  \n"
            "- **sources**: 응답 근거 목록  \n"
            "- **delta**: 응답 텍스트 조각  \n"
            "- **done**: 응답 완료 요약 정보  \n"
            "- **error**: 오류 안내"
        ),
        examples=["session", "sources", "delta", "done", "error"]
    )
    data: dict = Field(
        ...,
        description="이벤트 데이터입니다. (JSON 문자열로 전송됩니다.)",
        examples=[{"content": "2026학년도 1학기 수강신청 기간은 "}]
    )

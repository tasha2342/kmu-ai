from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.enum import ChatLanguage, ChatSessionStatus


def _reject_null_bytes(value: Optional[str]) -> Optional[str]:
    """널바이트(0x00)가 섞인 문자열을 거부합니다.

    PostgreSQL의 text 타입은 널바이트를 저장할 수 없어, 그대로 흘려보내면 DB 계층에서
    `ValueError: A string literal cannot contain NUL (0x00) characters.`가 발생해 500이 됩니다.
    사용자가 임의로 500을 유발할 수 있으므로 입력 검증 단계에서 422로 차단합니다.
    """

    if value is not None and "\x00" in value:
        raise ValueError("널바이트(0x00)는 사용할 수 없습니다.")
    return value


class CreateChatSessionPayload(BaseModel):
    """대화 세션 생성 요청"""

    title: Optional[str] = Field(
        None, max_length=255,
        description=(
            "세션 제목입니다.  \n"
            "생략하면 첫 질문을 기준으로 자동 생성됩니다."
        ),
        examples=["수강신청 문의"]
    )

    _no_null_title = field_validator("title")(_reject_null_bytes)
    language: ChatLanguage = Field(
        ChatLanguage.AUTO,
        description=(
            "대화 언어입니다. 챗봇은 이 언어로 답변합니다. (KAI-REQ-029)  \n"
            "- **auto**(기본값): 질문마다 발화 언어를 감지해 그 언어로 답변합니다.  \n"
            "- 그 외 값: 질문 언어와 무관하게 지정한 언어로만 답변합니다."
        ),
        examples=list(ChatLanguage)
    )
    profile: Optional[dict] = Field(
        None,
        description=(
            "개인화 컨텍스트 스냅샷입니다.  \n"
            "학내 개인정보 API가 제공되기 전까지는 클라이언트가 전달한 값을 그대로 보관만 합니다."
        ),
        examples=[{"grade": 3, "major": "컴퓨터공학전공"}]
    )


class UpdateChatSessionPayload(BaseModel):
    """대화 세션 수정 요청"""

    title: Optional[str] = Field(
        None, min_length=1, max_length=255,
        description="변경할 세션 제목입니다.",
        examples=["장학금 문의"]
    )

    _no_null_title = field_validator("title")(_reject_null_bytes)
    status: Optional[ChatSessionStatus] = Field(
        None,
        description=(
            "변경할 세션 상태입니다.  \n"
            "- **closed**: 사용자가 대화를 종료합니다.  \n"
            "- **active**: 종료된 대화를 다시 진행 상태로 되돌립니다.  \n"
            "- **idle_closed**는 미입력 자동 종료 시 서버가 설정하므로 직접 지정할 수 없습니다. (KAI-REQ-039)"
        ),
        examples=[ChatSessionStatus.CLOSED, ChatSessionStatus.ACTIVE]
    )


class SendChatMessagePayload(BaseModel):
    """대화 메시지 전송 요청"""

    message: str = Field(
        ..., min_length=1, max_length=4000,
        description="사용자 질문입니다.",
        examples=["2026학년도 1학기 수강신청 기간이 언제인가요?"]
    )

    _no_null_message = field_validator("message")(_reject_null_bytes)
    session_id: Optional[str] = Field(
        None,
        description=(
            "대화를 이어갈 세션 ID입니다.  \n"
            "생략하면 세션을 자동으로 생성합니다."
        ),
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    language: Optional[ChatLanguage] = Field(
        None,
        description=(
            "이번 질문에 대한 응답 언어입니다. (KAI-REQ-029)  \n"
            "- **auto**: 이번 질문의 언어를 감지해 그 언어로 답변합니다.  \n"
            "- **ko/en/zh/vi**: 질문 언어와 무관하게 지정한 언어로 답변합니다. (자동 감지보다 우선)  \n"
            "- 생략: 세션에 설정된 언어를 따릅니다."
        ),
        examples=list(ChatLanguage)
    )
    stream: bool = Field(
        True,
        description=(
            "응답을 SSE(Server-Sent Events)로 스트리밍할지 여부입니다.  \n"
            "- **true**: `text/event-stream`으로 응답 조각을 순차 전송합니다.  \n"
            "- **false**: 응답이 완성된 뒤 JSON으로 한 번에 반환합니다."
        ),
        examples=[True, False]
    )
    attachments: Optional[list[dict]] = Field(
        None,
        description=(
            "첨부 파일 목록입니다. (멀티모달 질의)  \n"
            "현재는 메시지에 함께 보관만 하며 모델 입력으로는 사용하지 않습니다."
        ),
        examples=[[{"file_name": "시간표.png", "file_type": "image/png"}]]
    )

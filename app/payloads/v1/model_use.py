from pydantic import BaseModel, Field

from typing import Optional, Union, Literal

from enum import Enum


# ===== Chat Completions =====

class ChatMessageRole(str, Enum):
    """채팅 메시지 역할"""
    
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    
    
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "system": "시스템 메시지",
                "user": "사용자 메시지",
                "assistant": "어시스턴트 메시지",
                "tool": "도구 메시지",
            }
        })
        return json_schema

class ChatMessage(BaseModel):
    """채팅 메시지"""
    
    role: ChatMessageRole = Field(
        ...,
        description="메시지 역할입니다.",
        examples=[ChatMessageRole.USER]
    )
    content: Optional[Union[str, list[dict]]] = Field(
        None,
        description="메시지 내용입니다.",
        examples=["안녕하세요!"]
    )
    tool_call_id: Optional[str] = Field(
        None,
        description=(
            "도구 호출 ID입니다.  \n"
            "⚠️ role이 'tool'인 경우 필수입니다."
		),
        examples=["call_abc123"]
    )
    tool_calls: Optional[list[dict]] = Field(
        None,
        description="도구 호출 목록입니다.",
        examples=[[{"id": "call_abc123", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}]]
    )

class ToolFunction(BaseModel):
    """도구 함수 정의"""
    
    name: str = Field(
        ...,
        description="함수 이름입니다.",
        examples=["get_weather"]
    )
    description: Optional[str] = Field(
        None,
        description="함수 설명입니다.",
        examples=["현재 날씨 정보를 가져옵니다."]
    )
    parameters: Optional[dict] = Field(
        None,
        description="함수 파라미터 JSON Schema입니다.",
        examples=[{"type": "object", "properties": {"location": {"type": "string"}}}]
    )

class Tool(BaseModel):
    """도구 정의"""
    
    type: Literal["function"] = Field(
        "function",
        description="도구 타입입니다.",
        examples=["function"]
    )
    function: ToolFunction = Field(
        ...,
        description="함수 정의입니다."
    )

class ResponseFormat(BaseModel):
    """응답 형식"""
    
    type: Literal["text", "json_object", "json_schema"] = Field(
        "text",
        description="응답 형식 타입입니다.",
        examples=["text", "json_object"]
    )
    json_schema: Optional[dict] = Field(
        None,
        description=(
            "JSON Schema 정의입니다.  \n"
            "⚠️ type이 'json_schema'인 경우 필수입니다."
		),
        examples=[{"name": "response", "schema": {"type": "object"}}]
    )

class ChatCompletionsPayload(BaseModel):
    """Chat Completions 요청 데이터"""
    
    model: str = Field(
        ...,
        description="사용할 모델명입니다.",
        examples=["gpt-4o-mini"]
    )
    messages: list[ChatMessage] = Field(
        ...,
        description="채팅 메시지 목록입니다."
    )
    temperature: Optional[float] = Field(
        None, ge=0.0, le=2.0,
        description="생성 다양성 조절값입니다. (0.0 ~ 2.0)",
        examples=[0.7]
    )
    top_p: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Top-p 샘플링 값입니다. (0.0 ~ 1.0)",
        examples=[1.0]
    )
    n: Optional[int] = Field(
        None, ge=1,
        description="생성할 응답 개수입니다.",
        examples=[1]
    )
    stream: Optional[bool] = Field(
        False,
        description="스트리밍 모드 사용 여부입니다.",
        examples=[False, True]
    )
    stop: Optional[Union[str, list[str]]] = Field(
        None,
        description="생성 중단 토큰입니다.",
        examples=["\\n"]
    )
    max_tokens: Optional[int] = Field(
        None, ge=1,
        description="최대 생성 토큰 수입니다.",
        examples=[1024]
    )
    max_completion_tokens: Optional[int] = Field(
        None, ge=1,
        description="최대 완성 토큰 수입니다.",
        examples=[1024]
    )
    presence_penalty: Optional[float] = Field(
        None, ge=-2.0, le=2.0,
        description="존재 페널티입니다. (-2.0 ~ 2.0)",
        examples=[0.0]
    )
    frequency_penalty: Optional[float] = Field(
        None, ge=-2.0, le=2.0,
        description="빈도 페널티입니다. (-2.0 ~ 2.0)",
        examples=[0.0]
    )
    tools: Optional[list[Tool]] = Field(
        None,
        description="사용할 도구 목록입니다."
    )
    tool_choice: Optional[Union[str, dict]] = Field(
        None,
        description="도구 선택 방식입니다.",
        examples=["auto", "none"]
    )
    response_format: Optional[ResponseFormat] = Field(
        None,
        description="응답 형식입니다."
    )
    seed: Optional[int] = Field(
        None,
        description="재현성을 위한 시드값입니다.",
        examples=[42]
    )
    chat_template_kwargs: Optional[dict] = Field(
        None,
        description="채팅 템플릿 추가 파라미터입니다.",
        examples=[{"enable_thinking": False}]
    )


# ===== Embeddings =====

class EmbeddingsPayload(BaseModel):
    """임베딩 요청 데이터"""
    
    model: str = Field(
        ...,
        description="사용할 모델명입니다.",
        examples=["text-embedding-3-small"]
    )
    input: Union[str, list[str], list[int], list[list[int]]] = Field(
        ...,
        description="임베딩할 텍스트입니다.",
        examples=["안녕하세요!"]
    )
    encoding_format: Optional[Literal["float", "base64"]] = Field(
        "float",
        description="임베딩 인코딩 형식입니다.",
        examples=["float"]
    )


# ===== Rerank =====

class RerankDocument(BaseModel):
    """문서 리랭크"""
    
    text: str = Field(
        ...,
        description="문서 텍스트입니다.",
        examples=["이것은 샘플 문서입니다."]
    )
    metadata: Optional[dict] = Field(
        None,
        description="문서 메타데이터입니다.",
        examples=[{"source": "web"}]
    )

class RerankPayload(BaseModel):
    """리랭크 요청 데이터"""
    
    model: str = Field(
        ...,
        description="사용할 모델명입니다.",
        examples=["rerank-english-v2.0"]
    )
    query: str = Field(
        ...,
        description="검색 쿼리입니다.",
        examples=["인공지능이란 무엇인가?"]
    )
    documents: list[Union[str, RerankDocument]] = Field(
        ...,
        description="리랭크할 문서 목록입니다.",
        examples=[["문서1", "문서2", "문서3"]]
    )
    top_n: Optional[int] = Field(
        None, ge=1,
        description="반환할 상위 문서 개수입니다.",
        examples=[3]
    )
    return_documents: Optional[bool] = Field(
        True,
        description="문서 내용 반환 여부입니다.",
        examples=[True, False]
    )

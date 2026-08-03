from pydantic import BaseModel, Field, ConfigDict

from typing import Any, Optional, Union, Literal

import app.utils.common as util


# ===== Models =====

class ModelInfo(BaseModel):
    """모델 정보"""
    
    id: str = Field(
        ...,
        description="모델 ID입니다.",
        examples=["gpt-4o-mini"]
    )
    created: int = Field(
        ...,
        description="생성 시간 (Unix timestamp)입니다.",
        examples=[1700000000]
    )
    object: Literal["model"] = Field(
        "model",
        description="객체 타입입니다.",
        examples=["model"]
    )
    owned_by: str = Field(
        ...,
        description="소유자입니다.",
        examples=["openai"]
    )

class ModelsListResponse(BaseModel):
    """모델 목록 응답"""
    
    object: Literal["list"] = Field(
        "list",
        description="객체 타입입니다.",
        examples=["list"]
    )
    data: list[ModelInfo] = Field(
        ...,
        description="모델 목록입니다."
    )


# ===== Chat Completions =====

class ChatCompletionMessage(BaseModel):
    """Chat Completion 메시지 (OpenAI 호환 + vLLM 확장 필드)"""
    
    model_config = ConfigDict(extra="allow")
    
    role: str = Field(
        "assistant",
        description="메시지 역할입니다.",
        examples=["assistant"]
    )
    content: Optional[str] = Field(
        None,
        description="메시지 내용입니다.",
        examples=["안녕하세요! 무엇을 도와드릴까요?"]
    )
    reasoning: Optional[str] = Field(
        None,
        description="추론(thinking) 텍스트입니다.",
    )
    reasoning_content: Optional[str] = Field(
        None,
        description="추론 단계 텍스트입니다. (일부 Reasoning 파서)",
    )
    annotations: Optional[list[Any]] = Field(
        None,
        description="OpenAI annotations입니다. (예: URL 인용 등)",
    )
    audio: Optional[dict[str, Any]] = Field(
        None,
        description="오디오 출력 메타데이터입니다.",
    )
    function_call: Optional[dict[str, Any]] = Field(
        None,
        description="레거시 함수 호출 객체입니다.",
    )
    tool_calls: Optional[list[dict[str, Any]]] = Field(
        None,
        description="도구 호출 목록입니다."
    )
    refusal: Optional[str] = Field(
        None,
        description="거부 메시지입니다."
    )

class ChatCompletionChoice(BaseModel):
    """Chat Completion 선택지 (OpenAI 호환 + vLLM 확장)"""
    
    model_config = ConfigDict(extra="allow")
    
    index: int = Field(
        ...,
        description="선택지 인덱스입니다.",
        examples=[0]
    )
    message: ChatCompletionMessage = Field(
        ...,
        description="완성된 메시지입니다."
    )
    finish_reason: Optional[str] = Field(
        None,
        description="완성 종료 이유입니다.",
        examples=["stop", "length", "tool_calls"]
    )
    logprobs: Optional[dict[str, Any]] = Field(
        None,
        description="로그 확률입니다."
    )
    stop_reason: Optional[str] = Field(
        None,
        description="중단 사유입니다.",
    )
    token_ids: Optional[list[int]] = Field(
        None,
        description="생성 토큰 ID 목록입니다.",
    )

class UsageInfo(BaseModel):
    """사용량 정보 (OpenAI 호환 + 세부 분해)"""
    
    model_config = ConfigDict(extra="allow")
    
    prompt_tokens: int = Field(
        ...,
        description="프롬프트 토큰 수입니다.",
        examples=[10]
    )
    completion_tokens: int = Field(
        ...,
        description="완성 토큰 수입니다.",
        examples=[20]
    )
    total_tokens: int = Field(
        ...,
        description="총 토큰 수입니다.",
        examples=[30]
    )
    prompt_tokens_details: Optional[dict[str, Any]] = Field(
        None,
        description="프롬프트 토큰 세부 정보입니다.",
    )
    completion_tokens_details: Optional[dict[str, Any]] = Field(
        None,
        description="완성 토큰 세부 정보입니다.",
    )

class ChatCompletionResponse(BaseModel):
    """Chat Completion 응답 (OpenAI 호환 + vLLM 확장)"""
    
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(
        ...,
        description="응답 ID입니다.",
        examples=["chatcmpl-abc123"]
    )
    created: int = Field(
        ...,
        description="생성 시간 (Unix timestamp)입니다.",
        examples=[1700000000]
    )
    object: Literal["chat.completion"] = Field(
        "chat.completion",
        description="객체 타입입니다.",
        examples=["chat.completion"]
    )
    model: str = Field(
        ...,
        description="사용된 모델명입니다.",
        examples=["gpt-4o-mini"]
    )
    choices: list[ChatCompletionChoice] = Field(
        ...,
        description="선택지 목록입니다."
    )
    usage: Optional[UsageInfo] = Field(
        None,
        description="사용량 정보입니다."
    )
    service_tier: Optional[str] = Field(
        None,
        description="서비스 등급입니다.",
    )
    system_fingerprint: Optional[str] = Field(
        None,
        description="시스템 지문입니다.",
        examples=["fp_abc123"]
    )
    prompt_logprobs: Optional[Any] = Field(
        None,
        description="프롬프트 로그 확률입니다.",
    )
    prompt_token_ids: Optional[list[int]] = Field(
        None,
        description="프롬프트 토큰 ID 목록입니다.",
    )
    kv_transfer_params: Optional[dict[str, Any]] = Field(
        None,
        description="KV 전달 관련 파라미터입니다.",
    )


# ===== Chat Completions Stream =====

class ChatCompletionStreamDelta(BaseModel):
    """Stream Delta 메시지 (OpenAI 호환 + vLLM 확장)"""
    
    model_config = ConfigDict(extra="allow")
    
    role: Optional[str] = Field(
        None,
        description="메시지 역할입니다.",
        examples=["assistant"]
    )
    content: Optional[str] = Field(
        None,
        description="메시지 내용입니다.",
        examples=["안녕"]
    )
    reasoning: Optional[str] = Field(
        None,
        description="스트리밍 추론 델타입니다.",
    )
    reasoning_content: Optional[str] = Field(
        None,
        description="스트리밍 추론 델타(대체 필드)입니다.",
    )
    annotations: Optional[list[Any]] = Field(
        None,
        description="annotations 델타입니다.",
    )
    audio: Optional[dict[str, Any]] = Field(
        None,
        description="오디오 델타입니다.",
    )
    function_call: Optional[dict[str, Any]] = Field(
        None,
        description="함수 호출 델타입니다.",
    )
    refusal: Optional[str] = Field(
        None,
        description="거부 델타입니다.",
    )
    tool_calls: Optional[list[dict[str, Any]]] = Field(
        None,
        description="도구 호출 목록입니다."
    )

class ChatCompletionStreamChoice(BaseModel):
    """Stream 선택지 (OpenAI 호환 + vLLM 확장)"""
    
    model_config = ConfigDict(extra="allow")
    
    index: int = Field(
        ...,
        description="선택지 인덱스입니다.",
        examples=[0]
    )
    delta: ChatCompletionStreamDelta = Field(
        ...,
        description="델타 메시지입니다."
    )
    finish_reason: Optional[str] = Field(
        None,
        description="완성 종료 이유입니다.",
        examples=["stop", "length", "tool_calls"]
    )
    logprobs: Optional[dict[str, Any]] = Field(
        None,
        description="로그 확률입니다."
    )
    stop_reason: Optional[str] = Field(
        None,
        description="중단 사유입니다.",
    )
    token_ids: Optional[list[int]] = Field(
        None,
        description="토큰 ID 델타입니다.",
    )

class ChatCompletionStreamResponse(BaseModel):
    """Chat Completion Stream 응답 (청크당 OpenAI + 확장 필드)"""
    
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(
        ...,
        description="응답 ID입니다.",
        examples=["chatcmpl-abc123"]
    )
    created: int = Field(
        ...,
        description="생성 시간 (Unix timestamp)입니다.",
        examples=[1700000000]
    )
    object: Literal["chat.completion.chunk"] = Field(
        "chat.completion.chunk",
        description="객체 타입입니다.",
        examples=["chat.completion.chunk"]
    )
    model: str = Field(
        ...,
        description="사용된 모델명입니다.",
        examples=["gpt-4o-mini"]
    )
    choices: list[ChatCompletionStreamChoice] = Field(
        ...,
        description="선택지 목록입니다."
    )
    system_fingerprint: Optional[str] = Field(
        None,
        description="시스템 지문입니다.",
        examples=["fp_abc123"]
    )
    usage: Optional[UsageInfo] = Field(
        None,
        description=(
            "사용량 정보입니다.",
            "⚠️ 스트림 마지막에만 포함됩니다."
		)
    )


# ===== Embeddings =====

class EmbeddingData(BaseModel):
    """임베딩 데이터"""
    
    object: Literal["embedding"] = Field(
        "embedding",
        description="객체 타입입니다.",
        examples=["embedding"]
    )
    index: int = Field(
        ...,
        description="인덱스입니다.",
        examples=[0]
    )
    embedding: Union[list[float], str] = Field(
        ...,
        description="임베딩 벡터입니다."
    )

class EmbeddingUsage(BaseModel):
    """임베딩 사용량"""
    
    prompt_tokens: int = Field(
        ...,
        description="프롬프트 토큰 수입니다.",
        examples=[10]
    )
    total_tokens: int = Field(
        ...,
        description="총 토큰 수입니다.",
        examples=[10]
    )

class EmbeddingsResponse(BaseModel):
    """임베딩 응답"""
    
    id: str = Field(
        ...,
        description="응답 ID입니다.",
        examples=[f"embd-{util.generate_id(k=16)}"]
    )
    created: int = Field(
        ...,
        description="생성 시간 (Unix timestamp)입니다.",
        examples=[1700000000]
    )
    object: Literal["list"] = Field(
        "list",
        description="객체 타입입니다.",
        examples=["list"]
    )
    data: list[EmbeddingData] = Field(
        ...,
        description="임베딩 데이터 목록입니다."
    )
    model: str = Field(
        ...,
        description="사용된 모델명입니다.",
        examples=["text-embedding-3-small"]
    )
    usage: EmbeddingUsage = Field(
        ...,
        description="사용량 정보입니다."
    )


# ===== Rerank =====

class RerankResult(BaseModel):
    """리랭크 결과"""
    
    index: int = Field(
        ...,
        description="원본 문서 인덱스입니다.",
        examples=[0]
    )
    relevance_score: float = Field(
        ...,
        description="관련성 점수입니다.",
        examples=[0.95]
    )
    document: Optional[Union[str, dict]] = Field(
        None,
        description="문서 내용입니다."
    )

class RerankResponse(BaseModel):
    """리랭크 응답"""
    
    id: str = Field(
        ...,
        description="응답 ID입니다.",
        examples=["rerank-abc123"]
    )
    model: str = Field(
        ...,
        description="사용된 모델명입니다.",
        examples=["rerank-english-v2.0"]
    )
    results: list[RerankResult] = Field(
        ...,
        description="리랭크 결과 목록입니다."
    )
    usage: Optional[dict] = Field(
        None,
        description="사용량 정보입니다."
    )

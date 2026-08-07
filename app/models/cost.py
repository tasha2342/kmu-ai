from pydantic import BaseModel, Field

from typing import Optional


class TextGenerationCost(BaseModel):
    """텍스트 생성 모델 비용 (per 1M tokens, USD)"""

    input_tokens: float = Field(
        ...,
        description="입력 토큰 당 비용입니다. (per 1M tokens, USD)",
        examples=[2.50]
    )
    cached_input_tokens: Optional[float] = Field(
        None,
        description="캐시된 입력 토큰 당 비용입니다. (per 1M tokens, USD)",
        examples=[0.25]
    )
    output_tokens: float = Field(
        ...,
        description="출력 토큰 당 비용입니다. (per 1M tokens, USD)",
        examples=[15.00]
    )


class EmbeddingCost(BaseModel):
    """임베딩 모델 비용 (per 1M tokens, USD)"""

    single_tokens: float = Field(
        ...,
        description="단건 요청 토큰 당 비용입니다. (per 1M tokens, USD)",
        examples=[0.20]
    )
    batch_tokens: Optional[float] = Field(
        None,
        description="배치 요청 토큰 당 비용입니다. (per 1M tokens, USD)",
        examples=[0.10]
    )


class RerankCost(BaseModel):
    """리랭크 모델 비용 (per 1K searches, USD)"""

    searches: float = Field(
        ...,
        description="검색 횟수 당 비용입니다. (per 1K searches, USD)",
        examples=[2.00]
    )


class TextGenerationUsage(BaseModel):
    """텍스트 생성 모델 사용량"""

    input_tokens: int = Field(
        0,
        description="입력 토큰 수입니다.",
        examples=[100]
    )
    cached_input_tokens: int = Field(
        0,
        description="캐시된 입력 토큰 수입니다.",
        examples=[50]
    )
    output_tokens: int = Field(
        0,
        description="출력 토큰 수입니다. (추론 토큰 포함)",
        examples=[50]
    )
    reasoning_tokens: int = Field(
        0,
        description="출력 토큰 중 추론(thinking)에 쓰인 토큰 수입니다.",
        examples=[14]
    )


class EmbeddingUsage(BaseModel):
    """임베딩 모델 사용량"""

    single_tokens: int = Field(
        0,
        description="단건 요청 토큰 수입니다.",
        examples=[100]
    )
    batch_tokens: int = Field(
        0,
        description="배치 요청 토큰 수입니다.",
        examples=[100]
    )


class RerankUsage(BaseModel):
    """리랭크 모델 사용량"""

from pydantic import BaseModel, Field

from typing import Optional, Union

from app.models.enum import ModelProvider, ModelType
from app.models.cost import TextGenerationCost, EmbeddingCost, RerankCost


class AddModelPayload(BaseModel):
    """모델 추가 요청 데이터"""
    
    name: str = Field(
        ...,
        description=(
            "모델명입니다. (사용자 정의)  \n"
            "⚠️ 모델명은 고유해야 합니다."
		),
        examples=["gpt-4o-mini"]
    )
    provider: ModelProvider = Field(
        ...,
        description="모델 제공자입니다.",
        examples=list(ModelProvider)
    )
    model_id: str = Field(
        ...,
        description="모델 ID입니다. (제공자의 모델 ID)",
        examples=["gpt-4o-mini"]
    )
    model_type: ModelType = Field(
        ...,
        description="모델 타입입니다.",
        examples=list(ModelType)
    )
    api_base: Optional[str] = Field(
        None,
        description="API Base URL입니다. (OpenAI Compatible용)",
        examples=["http://192.168.0.100:8000/v1"]
    )
    api_key: Optional[str] = Field(
        None,
        description="API Key입니다.",
        examples=["jd-TVlMaVY5aXduSkYyUENGQnJSYkhJSjVRZDExMDdiNGUtOTM2"]
    )
    description: Optional[str] = Field(
        None,
        description="모델 설명입니다.",
        examples=["OpenAI의 최신 모델"]
    )
    cost: Optional[Union[
        TextGenerationCost,
        EmbeddingCost,
        RerankCost,
    ]] = Field(
        None,
        description=(
            "모델 비용입니다.  \n"
            "모델 타입에 따라 비용 구조가 다릅니다.  \n"
            "text_generation: {input_tokens, cached_input_tokens, output_tokens}  \n"
            "embedding: {single_tokens, batch_tokens}  \n"
            "rerank: {searches}"
        ),
        examples=[
            TextGenerationCost(input_tokens=2.50, cached_input_tokens=0.25, output_tokens=15.00),
            EmbeddingCost(single_tokens=0.20, batch_tokens=0.10),
            RerankCost(searches=2.00)
        ]
    )
    config: Optional[dict] = Field(
        default_factory=dict,
        description="모델 설정입니다. (JSON 형식)",
        examples=[{"temperature": 0.7}]
    )


class UpdateModelPayload(BaseModel):
    """모델 수정 요청 데이터"""
    
    api_base: Optional[str] = Field(
        None,
        description="API Base URL입니다. (OpenAI Compatible용)",
        examples=["http://192.168.0.100:8000/v1"]
    )
    api_key: Optional[str] = Field(
        None,
        description="API Key입니다.",
        examples=["jd-TVlMaVY5aXduSkYyUENGQnJSYkhJSjVRZDExMDdiNGUtOTM2"]
    )
    description: Optional[str] = Field(
        None,
        description="모델 설명입니다.",
        examples=["OpenAI의 최신 모델"]
    )
    cost: Optional[Union[
        TextGenerationCost,
        EmbeddingCost,
        RerankCost,
    ]] = Field(
        None,
        description=(
            "모델 비용입니다.  \n"
            "모델 타입에 따라 비용 구조가 다릅니다.  \n"
            "text_generation: {input_tokens, cached_input_tokens, output_tokens}  \n"
            "embedding: {single_tokens, batch_tokens}  \n"
            "rerank: {searches}"
        ),
        examples=[
            TextGenerationCost(input_tokens=2.50, cached_input_tokens=0.25, output_tokens=15.00),
            EmbeddingCost(single_tokens=0.20, batch_tokens=0.10),
            RerankCost(searches=2.00)
        ]
    )
    config: Optional[dict] = Field(
        None,
        description="모델 설정입니다. (JSON 형식)",
        examples=[{"temperature": 0.7}]
    )


class RunModelPayload(BaseModel):
    """모델 실행 요청 데이터"""
    
    node_id: Optional[str] = Field(
        None,
        description=(
            "실행할 노드의 ID입니다.  \n"
            "컨테이너에 직접 올라가는 로컬 임베딩 모델은 GPU 노드를 쓰지 않으므로 지정하지 않아도 됩니다."
        ),
        examples=["e7acfecb65d54c72b0e1b94ad6709f87"]
    )
    device_ids: Optional[list[int]] = Field(
        None,
        description=(
            "할당할 디바이스(GPU) ID 목록입니다.  \n"
            "지정하지 않으면 모든 디바이스에 할당됩니다."
        ),
        examples=[[0, 1], [1]]
    )
    gpu_memory_utilization: Optional[float] = Field(
        0.8, ge=0.0, le=1.0,
        description="각 디바이스(GPU)에서 사용할 최대 메모리 비율입니다. 0.0 ~ 1.0 사이의 값으로 지정합니다.",
        examples=[0.5, 0.75, 1.0]
    )

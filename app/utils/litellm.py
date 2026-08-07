import logging
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

import litellm
litellm.suppress_debug_info = True
litellm.drop_params = True
litellm.client_session = None  # 기존 세션 재사용 비활성화

import time

from huggingface_hub import HfApi

from typing import AsyncGenerator, Optional, Union

from pydantic import BaseModel

from litellm.types.utils import EmbeddingResponse, Usage

from app.config import env
from app.models.auth import TokenUserInfo
from app.models.enum import ModelProvider, ModelStatus, ModelType, ModelUsageStatus
from app.models.cost import TextGenerationUsage, EmbeddingUsage, RerankUsage
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.embedder import get_embedder
from app.utils.logger import get_logger
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.common as util


logger = get_logger("litellm", log_dir="logs")


# RUNNING 모델 조회 캐시. 챗봇 한 턴이 classify → generate로 같은 모델을 두 번 조회하는데,
# 그 사이 검색이 DB 풀을 잡아먹으면 두 번째 조회가 503으로 실패해 model_error가 된다.
_RUNNING_MODEL_CACHE_TTL_SEC = 60.0
_running_model_cache: dict[str, tuple[float, "db_items.Model"]] = {}


async def get_running_model(
    model_name: str,
    db_manager: DatabaseManager,
    *,
    use_cache: bool = True,
) -> Optional[db_items.Model]:
    """RUNNING 상태의 등록 모델을 조회합니다. 짧은 TTL 캐시로 반복 조회를 줄입니다."""

    now = time.time()
    if use_cache:
        cached = _running_model_cache.get(model_name)
        if cached and (now - cached[0]) < _RUNNING_MODEL_CACHE_TTL_SEC:
            return cached[1]

    query = (db_models.Model.select().where(
        (db_models.Model.name == model_name) &
        (db_models.Model.status == ModelStatus.RUNNING.value)
    ))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if model and use_cache:
        _running_model_cache[model_name] = (now, model)
    elif use_cache:
        _running_model_cache.pop(model_name, None)
    return model


class ModelValidationResult(BaseModel):
    """모델 검증 결과"""
    
    success: bool
    """검증 성공 여부"""
    message: str
    """검증 결과 메시지"""
    error_type: Optional[str] = None
    """오류 타입 (실패 시)"""


def get_litellm_model_name(provider: ModelProvider, model_name: str, model_id: str) -> str:
    """LiteLLM 모델명을 생성합니다.
    
    Args:
        provider (ModelProvider): 모델 제공자
        model_name (str): 모델 이름
        model_id (str): 모델 ID
        
    Returns:
        LiteLLM 형식의 모델명
    """
    
    if provider == ModelProvider.LOCAL:
        return f"openai/{model_name}"
    elif provider == ModelProvider.OPENAI_COMPATIBLE:
        return f"openai/{model_id}"
    return f"{provider.value}/{model_id}"

def get_litellm_params(model: db_items.Model, user_info: TokenUserInfo) -> dict:
    """LiteLLM API 호출에 필요한 파라미터를 생성합니다.
    
    Args:
        model (db_items.Model): 모델 정보
        user_info (TokenUserInfo): 사용자 정보
        
    Returns:
        LiteLLM API 파라미터 딕셔너리
    """
    
    params = {}
    
    if model.api_key:
        if model.provider == ModelProvider.LOCAL:
            params["api_key"] = user_info.token
        else:
            params["api_key"] = model.api_key
    if model.api_base:
        params["api_base"] = model.api_base
        if not params["api_base"].endswith("/v1"):
            params["api_base"] = params["api_base"].rstrip("/") + "/v1"
    
    params["num_retries"] = 0
    
    if model.config:
        for key, value in model.config.items():
            params[key] = value
    
    return params

def extract_token_counts(usage) -> tuple[int, int, int, int]:
    """usage 객체에서 토큰 수를 추출합니다.

    Gemma 4는 thinking을 끌 수 없다. (`thinkingBudget`/`thinkingLevel` 미지원이고
    `includeThoughts=false`로도 thought 파트가 그대로 온다) 따라서 이 모델을 쓰면 추론 토큰이
    항상 발생하는데, Gemini API는 이를 `thoughtsTokenCount`로 분리해서 보고한다.

    LiteLLM 버전·제공자에 따라 이 값이 `completion_tokens`에 이미 포함된 경우와 빠져 있는 경우가
    모두 있어서, `total_tokens`와 대조해 빠져 있을 때만 더한다. 과금은 추론 토큰도 출력 토큰으로
    계산되므로 `output_tokens`에는 항상 포함된 값이 들어가야 대시보드 집계가 맞는다.

    Args:
        usage: LiteLLM 응답의 usage 객체 (None 가능)

    Returns:
        tuple[int, int, int, int]: (입력 토큰, 캐시된 입력 토큰, 출력 토큰, 추론 토큰)
    """

    if not usage:
        return 0, 0, 0, 0

    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "completion_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or 0

    cached_input_tokens = 0
    prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
    if prompt_tokens_details:
        cached_input_tokens = getattr(prompt_tokens_details, "cached_tokens", 0) or 0

    reasoning_tokens = 0
    completion_tokens_details = getattr(usage, "completion_tokens_details", None)
    if completion_tokens_details:
        reasoning_tokens = getattr(completion_tokens_details, "reasoning_tokens", 0) or 0

    if reasoning_tokens:
        # completion_tokens가 추론 토큰을 빼고 보고된 경우에만 보정한다.
        if total_tokens >= input_tokens + output_tokens + reasoning_tokens:
            output_tokens += reasoning_tokens
    elif total_tokens > input_tokens + output_tokens:
        # 추론 토큰을 따로 보고하지 않는 경로(Google의 OpenAI 호환 엔드포인트 등)에서는
        # completion_tokens에 thought 토큰이 빠진 채로 오고 total_tokens에만 반영된다.
        # (실측: prompt=12, completion=1, total=57) 차이를 추론 토큰으로 간주해 복원한다.
        reasoning_tokens = total_tokens - input_tokens - output_tokens
        output_tokens += reasoning_tokens

    return input_tokens, cached_input_tokens, output_tokens, reasoning_tokens

async def validate_model(
    provider: ModelProvider,
    model_name: str,
    model_id: str,
    model_type: ModelType,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
) -> ModelValidationResult:
    """모델 설정이 유효한지 검증합니다.
    
    LiteLLM을 사용하여 실제 API 호출을 통해 검증합니다.
    
    Args:
        provider (ModelProvider): 모델 제공자
        model_name (str): 모델 이름
        model_id (str): 모델 ID
        model_type (ModelType): 모델 타입
        api_key (Optional[str]): API Key (Default: None)
        api_base (Optional[str]): API Base URL (Default: None)
        
    Returns:
        ModelValidationResult: 검증 결과
    """
    
    # 로컬 모델 검증
    if provider == ModelProvider.LOCAL:
        hf_api = HfApi(token=api_key)
        
        model_info = hf_api.model_info(model_id)
        architectures = model_info.config.get("architectures", []) if model_info.config else []
        
        if not architectures:
            return ModelValidationResult(
                success=False,
                message="지원하지 않는 모델입니다.",
                error_type="unsupported_model"
            )
        
        return ModelValidationResult(
            success=True,
            message="로컬 모델 검증에 성공했습니다."
        )
    
    # LiteLLM 모델명 생성
    litellm_model = get_litellm_model_name(provider, model_name, model_id)
    
    # LiteLLM 파라미터 구성
    params = {}
    if api_key:
        params["api_key"] = api_key
    if api_base:
        params["api_base"] = api_base
    
    try:
        if model_type == ModelType.TEXT_GENERATION:
            # 텍스트 생성 모델 검증
            response = await litellm.acompletion(
                model=litellm_model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=16,
                **params
            )
            
            if response and response.choices:
                return ModelValidationResult(
                    success=True,
                    message="텍스트 생성 모델 검증에 성공했습니다."
                )
            else:
                return ModelValidationResult(
                    success=False,
                    message="텍스트 생성 모델 응답이 올바르지 않습니다.",
                    error_type="invalid_response"
                )
        elif model_type == ModelType.EMBEDDING:
            # 임베딩 모델 검증
            response = await litellm.aembedding(
                model=litellm_model,
                input=["test"],
                **params
            )
            
            if response and response.data:
                return ModelValidationResult(
                    success=True,
                    message="임베딩 모델 검증에 성공했습니다."
                )
            else:
                return ModelValidationResult(
                    success=False,
                    message="임베딩 모델 응답이 올바르지 않습니다.",
                    error_type="invalid_response"
                )
        elif model_type == ModelType.RERANK:
            # 리랭크 모델 검증
            response = await litellm.arerank(
                model=litellm_model,
                query="test query",
                documents=["test document"],
                **params
            )
            
            if response and hasattr(response, "results"):
                return ModelValidationResult(
                    success=True,
                    message="리랭크 모델 검증에 성공했습니다."
                )
            else:
                return ModelValidationResult(
                    success=False,
                    message="리랭크 모델 응답이 올바르지 않습니다.",
                    error_type="invalid_response"
                )
        else:
            return ModelValidationResult(
                success=False,
                message="지원하지 않는 모델입니다.",
                error_type="unsupported_model"
            )
    except Exception as exc:
        error_str = str(exc).lower()
        
        # 오류 유형 분류
        if "api key" in error_str or "authentication" in error_str or "unauthorized" in error_str or "401" in error_str:
            error_type = "invalid_api_key"
            message = "모델 제공자 서버의 API Key가 올바르지 않습니다."
        elif "not found" in error_str or "does not exist" in error_str or "404" in error_str:
            error_type = "model_not_found"
            message = "모델 제공자 서버에서 모델을 찾을 수 없습니다."
        elif "connection" in error_str or "timeout" in error_str or "unreachable" in error_str:
            error_type = "connection_error"
            message = "모델 제공자 서버에 연결할 수 없습니다."
        elif "rate limit" in error_str or "429" in error_str:
            # Rate limit은 연결은 성공한 것이므로 검증 통과로 처리
            return ModelValidationResult(
                success=True,
                message="모델 검증에 성공했습니다. (Rate limit으로 인해 제한적 검증)"
            )
        elif "permission" in error_str or "403" in error_str:
            error_type = "permission_denied"
            message = "해당 모델에 대한 접근 권한이 없습니다."
        else:
            error_type = "unknown_error"
            message = "모델 검증 중 오류가 발생했습니다."
            logger.exception(f"모델 검증 실패 - provider: {provider}, model_id: {model_id}")
        
        return ModelValidationResult(
            success=False,
            message=message,
            error_type=error_type
        )

async def create_embedding(
    model: db_items.Model,
    user_info: TokenUserInfo,
    inputs: Union[str, list],
    **extra,
) -> EmbeddingResponse:
    """임베딩을 생성합니다.

    `ModelProvider.LOCAL` 임베딩 모델은 GPU 노드나 외부 추론 서버로 나가지 않고
    이 컨테이너 안에서(`app.utils.embedder`) 바로 처리한다. 그 외 제공자는 기존대로
    LiteLLM으로 위임한다. 어느 쪽이든 반환 타입이 같으므로 호출부는 구분할 필요가 없다.

    Args:
        model (db_items.Model): 임베딩 모델 정보
        user_info (TokenUserInfo): 사용자 정보
        inputs (list[str]): 임베딩할 텍스트 목록
        **extra: LiteLLM에 그대로 전달할 추가 파라미터 (LOCAL 모델은 무시)

    Returns:
        EmbeddingResponse: 임베딩 응답 (OpenAI 호환 구조)
    """

    if model.provider == ModelProvider.LOCAL:
        texts = [inputs] if isinstance(inputs, str) else list(inputs)
        if any(not isinstance(text, str) for text in texts):
            raise ValueError(
                "컨테이너 내장 임베딩 모델은 토큰 ID 입력을 지원하지 않습니다. 문자열로 보내주세요."
            )

        embedder = await get_embedder(model.model_id)
        vectors, total_tokens = await embedder.encode(texts)

        return EmbeddingResponse(
            model=model.name,
            data=[
                {"object": "embedding", "index": index, "embedding": vector}
                for index, vector in enumerate(vectors)
            ],
            usage=Usage(prompt_tokens=total_tokens, total_tokens=total_tokens),
        )

    return await litellm.aembedding(
        model=get_litellm_model_name(model.provider, model.name, model.model_id),
        input=inputs,
        **get_litellm_params(model, user_info),
        **extra,
    )


async def save_usage(
    user_info: TokenUserInfo,
    model_name: str,
    model_type: ModelType,
    usage: Optional[Union[
        TextGenerationUsage,
        EmbeddingUsage,
        RerankUsage,
    ]] = None,
    request_id: Optional[str] = None,
    latency_ms: int = 0,
    status: ModelUsageStatus = ModelUsageStatus.SUCCESS,
    error_message: Optional[str] = None,
    metadata: Optional[dict] = None,
    db_manager: Optional[DatabaseManager] = None,
):
    """모델 사용량을 저장합니다.
    
    Args:
        user_info (TokenUserInfo): 사용자 정보
        model_name (str): 모델명
        model_type (ModelType): 모델 타입
        usage (Optional[Union[TextGenerationUsage, EmbeddingUsage, RerankUsage]]): 사용량 (모델 타입에 따라 구조가 다름) (Default: None)
        request_id (Optional[str]): 요청 ID (Default: None)
        latency_ms (int): 지연 시간 (밀리초) (Default: 0)
        status (ModelUsageStatus): 상태 코드 (Default: ModelUsageStatus.SUCCESS)
        error_message (Optional[str]): 오류 메시지 (Default: None)
        metadata (Optional[dict]): 추가 메타데이터 (Default: None)
        db_manager (Optional[DatabaseManager]): 데이터베이스 매니저 (Default: None)
    """
    
    try:
        if db_manager is None:
            db_manager = get_db_manager()
        
        query = db_models.ModelUsage.insert(
            user_name=user_info.username,
            model_name=model_name,
            model_type=model_type.value,
            usage=usage.model_dump() if usage else None,
            request_id=request_id,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
            metadata=metadata
        )
        await db_manager.execute_query(query)
    except Exception:
        logger.exception("모델 사용량 저장 중 오류가 발생했습니다.")


def build_chat_template_kwargs(provider: ModelProvider, requested: Optional[dict] = None) -> Optional[dict]:
    """LOCAL/OPENAI_COMPATIBLE 모델 호출 시 chat_template_kwargs를 구성합니다.

    호출자가 명시한 값(예: enable_thinking)이 있으면 그대로 두고, 없는 키만 DEFAULT_ENABLE_THINKING(.env)로 채운다.
    """

    if provider not in (ModelProvider.LOCAL, ModelProvider.OPENAI_COMPATIBLE):
        return None

    kwargs = dict(requested or {})
    if "enable_thinking" not in kwargs and env.DEFAULT_ENABLE_THINKING is not None:
        kwargs["enable_thinking"] = env.DEFAULT_ENABLE_THINKING
    return kwargs or None


async def stream_chat_completion(
    model_name: str,
    messages: list[dict],
    user_info: TokenUserInfo,
    db_manager: DatabaseManager,
    chat_template_kwargs: Optional[dict] = None,
    usage_source: str = "internal",
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """모델을 조회해 litellm으로 스트리밍 완료를 요청하고, 텍스트 델타만 yield합니다.

    `/v1/chat/completions`(model_use.py)는 OpenAI 호환 스트림 청크 전체(tool_calls/reasoning 등 포함)를
    그대로 재구성해 외부에 돌려줘야 하지만, 이 헬퍼는 텍스트만 필요한 내부 호출자(예: 챗봇 오케스트레이션
    그래프)를 위한 것이다. 모델 조회, LiteLLM 호출 파라미터 구성, `chat_template_kwargs` 기본값 병합,
    `save_usage` 기록은 그쪽과 동일한 로직을 공유해 usage/cost 대시보드가 계속 정확하게 집계되도록 한다.

    Raises:
        ValueError: 등록되지 않았거나 실행 중이 아닌 모델인 경우
    """

    model = await get_running_model(model_name, db_manager)
    if not model:
        raise ValueError(f"등록되지 않은 모델이거나 실행중이 아닙니다: model={model_name}")

    completion_params = {
        "model": get_litellm_model_name(model.provider, model.name, model.model_id),
        "messages": messages,
        "stream": True,
        **get_litellm_params(model, user_info),
    }

    merged_kwargs = build_chat_template_kwargs(model.provider, chat_template_kwargs)
    if merged_kwargs:
        completion_params["chat_template_kwargs"] = merged_kwargs
    if max_tokens is not None:
        completion_params["max_tokens"] = max_tokens

    request_id = f"chatcmpl-{util.generate_id(k=16)}"
    start_time = time.time()
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0

    try:
        response = await litellm.acompletion(**completion_params)
        async for chunk in response:
            for choice in chunk.choices:
                d = choice.delta if hasattr(choice, "delta") else None
                if not d:
                    continue

                # 추론(thinking) 델타는 최종 답변이 아니므로 내부 호출자에게 흘리지 않는다.
                # Gemma 4처럼 thinking을 끌 수 없는 모델은 이 델타가 항상 함께 들어온다.
                if getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None):
                    continue

                content = getattr(d, "content", None)
                if content:
                    yield content

            if hasattr(chunk, "usage") and chunk.usage:
                (
                    input_tokens,
                    cached_input_tokens,
                    output_tokens,
                    reasoning_tokens,
                ) = extract_token_counts(chunk.usage)

        await save_usage(
            user_info=user_info,
            model_name=model_name,
            model_type=ModelType.TEXT_GENERATION,
            usage=TextGenerationUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
            ),
            request_id=request_id,
            latency_ms=int((time.time() - start_time) * 1000),
            metadata={"stream": True, "source": usage_source},
            db_manager=db_manager,
        )
    except Exception as exc:
        await save_usage(
            user_info=user_info,
            model_name=model_name,
            model_type=ModelType.TEXT_GENERATION,
            request_id=request_id,
            latency_ms=int((time.time() - start_time) * 1000),
            status=ModelUsageStatus.ERROR,
            error_message=str(exc)[:1024],
            metadata={"stream": True, "source": usage_source},
            db_manager=db_manager,
        )
        raise
